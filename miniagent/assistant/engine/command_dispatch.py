"""Engine — 统一命令调度器

CLI 和飞书共享的命令路由，使用 `/` 前缀。

核心特性：
- print 捕获：CLI 命令原本用 print()，飞书需要返回字符串
- 不中断：`/status` 等检查命令不会打断正在运行的 agent
- 远程约束：飞书侧 `capture=True` 时默认 `allow_session_mutations_when_capture=False`，
  阻止 `/session switch/create/rename` 与 `/schedule` 变异；`feishu.dot_commands_full=true` 时放开
- 模糊匹配：未知命令会提示最接近的有效命令（用户体验增强）

命令全集与用户说明见 ``docs/CLI.md``；飞书约束见 ``docs/FEISHU.md``。
"""

from __future__ import annotations

import difflib
import io
import json
import os
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

from miniagent.agent.constants import IMPROVE_MAX_ITERATIONS
from miniagent.agent.logging import get_logger
from miniagent.agent.prompts.improver import IMPROVE_PROMPT
from miniagent.agent.prompts.reviewer import REVIEW_ITERATION_PROMPT, REVIEW_PROMPT
from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.command_registry import COMMAND_REGISTRY, CommandHandler

_logger = get_logger(__name__)


# ─── 已注册命令列表（用于模糊匹配与 CLI 补全）────────────────────────────────
# 顺序影响 _find_command_by_prefix：同前缀时返回列表中先出现的项
# （例如 "/sta" 会匹配 "/stats" 而非 "/status"，因 "/stats" 更靠前）。
_REGISTERED_COMMANDS = list(COMMAND_REGISTRY.names)


def _find_closest_command(input_cmd: str, threshold: float = 0.6) -> str | None:
    """使用模糊匹配查找最接近的命令。

    Args:
        input_cmd: 用户输入的命令（如 "/sttatus"）
        threshold: 最小相似度阈值（0.6 = 60%匹配）

    Returns:
        最接近的命令，或 None（无匹配）
    """
    matches = difflib.get_close_matches(
        input_cmd.lower(),
        [cmd.lower() for cmd in _REGISTERED_COMMANDS],
        n=1,
        cutoff=threshold,
    )
    if matches:
        # 返回原始大小写的命令
        for cmd in _REGISTERED_COMMANDS:
            if cmd.lower() == matches[0]:
                return cmd
    return None


def _find_command_by_prefix(input_cmd: str) -> str | None:
    """前缀匹配（至少3字符）。

    多个命令共享同一前缀时，返回 ``_REGISTERED_COMMANDS`` 中**最先**匹配的一项，
    而非语义上「最可能」的命令。

    Args:
        input_cmd: 用户输入的命令前缀（如 "/sta")

    Returns:
        匹配的完整命令，或 None
    """
    input_lower = input_cmd.lower()
    if len(input_lower) < 4:  # "/" + 至少3字符
        return None
    for cmd in _REGISTERED_COMMANDS:
        if cmd.lower().startswith(input_lower):
            return cmd
    return None


# ─── ANSI 颜色到 CLI 样式类的映射 ────────────────────────────────────────────
# 用于 _write 函数将 ANSI 颜色名转换为 cli_transcript_append 所需的样式类
_ANSI_COLOR_TO_STYLE = {
    "ansicyan": "class:cli-user-title",
    "ansigreen": "class:cli-ok",
    "ansired": "class:cli-err",
    "ansiyellow": "class:cli-warn",
    "ansiblue": "class:cli-default",
    "ansimagenta": "class:cli-default",
    "ansiwhite": "class:cli-default",
    "ansibrightcyan": "class:cli-user-title",
    "ansibrightgreen": "class:cli-ok",
    "ansibrightred": "class:cli-err",
    "ansibrightyellow": "class:cli-warn",
    "": "class:cli-default",
}


_REMOTE_SESSION_HINT = (
    "⚠️ 该命令会修改与 CLI 共享的会话状态，请在本地 MiniAgent 终端执行。\n"
    "飞书上可使用 /session list 查看会话列表。"
)

def _normalize_command_text(text: str) -> str | None:
    """规范化命令文本；非命令输入返回 None。"""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("/"):
        return stripped
    return None


from miniagent.assistant.engine.commands.basic_commands import (
    handle_config,
    handle_doctor,
    handle_help,
    handle_model,
    handle_reload_config,
    handle_schedule,
    handle_stats,
    handle_status,
)
from miniagent.assistant.engine.commands.confirmation_commands import handle_confirmation
from miniagent.assistant.engine.commands.instance_commands import handle_instance
from miniagent.assistant.engine.commands.knowledge_commands import handle_knowledge
from miniagent.assistant.engine.commands.quality_commands import handle_improve, handle_review
from miniagent.assistant.engine.commands.runtime_commands import (
    handle_abort,
    handle_background_task,
    handle_feishu,
    handle_query,
    handle_queue,
    handle_reload_skills,
    handle_stop,
)
from miniagent.assistant.engine.commands.self_opt_commands import handle_self_opt
from miniagent.assistant.engine.commands.session_commands import handle_session
from miniagent.assistant.engine.commands.test_commands import handle_test

_BOUND_HANDLERS: dict[str, CommandHandler] = {
    "abort": handle_abort,
    "adjust": handle_confirmation,
    "background_task": handle_background_task,
    "config": handle_config,
    "confirm": handle_confirmation,
    "doctor": handle_doctor,
    "feishu": handle_feishu,
    "help": handle_help,
    "improve": handle_improve,
    "instance": handle_instance,
    "knowledge": handle_knowledge,
    "model": handle_model,
    "query": handle_query,
    "queue": handle_queue,
    "reject": handle_confirmation,
    "reload_config": handle_reload_config,
    "reload_skills": handle_reload_skills,
    "review": handle_review,
    "schedule": handle_schedule,
    "self_opt": handle_self_opt,
    "session": handle_session,
    "stats": handle_stats,
    "status": handle_status,
    "stop": handle_stop,
    "test": handle_test,
}
BOUND_COMMAND_REGISTRY = COMMAND_REGISTRY.bind_handlers(_BOUND_HANDLERS)


async def dispatch_command(
    text: str,
    *,
    state: CliLoopState | dict[str, Any],
    engine: Any = None,
    registry: Any = None,
    monitor: Any = None,
    skill_toolboxes: list | None = None,
    skill_prompts: list | None = None,
    capture: bool = False,
    allow_session_mutations_when_capture: bool = True,
    feishu_user_status: Callable[[str], None] | None = None,
    message_queue_abort_chat_id: str | None = None,
    confirmation_session_key: str | None = None,
) -> str | None:
    """通过不可变命令注册表解析、授权并调用独立处理器。"""
    normalized = _normalize_command_text(text)
    if normalized is None:
        return None
    command_name = normalized.split(maxsplit=1)[0].lower()
    handler = BOUND_COMMAND_REGISTRY.handler_for(command_name)
    if handler is not None:
        return await handler(
            normalized,
            state=state,
            engine=engine,
            registry=registry,
            monitor=monitor,
            skill_toolboxes=skill_toolboxes,
            skill_prompts=skill_prompts,
            capture=capture,
            allow_session_mutations_when_capture=allow_session_mutations_when_capture,
            feishu_user_status=feishu_user_status,
            message_queue_abort_chat_id=message_queue_abort_chat_id,
            confirmation_session_key=confirmation_session_key,
        )
    closest = _find_command_by_prefix(command_name) or _find_closest_command(command_name)
    if closest and closest.lower() != command_name:
        suggestion = (
            f"{WARNING_PREFIX} 未找到命令 '{command_name}'，您是否想输入 '{closest}'？"
        )
        if capture:
            return suggestion
        print(suggestion)
    return None


def _capture(fn: Callable[[], None]) -> str:
    """捕获 print 输出并返回字符串。"""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            fn()
    except Exception as e:
        return f"{ERROR_PREFIX} 命令执行失败: {e}"
    return buf.getvalue().strip()


# ─── /review 辅助函数 ───────────────────────────────

# REVIEW_PROMPT 和 REVIEW_ITERATION_PROMPT 现在从 miniagent.agent.prompts.reviewer 导入
# 使用 XML 标签结构化，遵循 Claude 最佳实践
_REVIEW_SYSTEM = REVIEW_PROMPT

_REVIEW_ITERATION_SYSTEM = REVIEW_ITERATION_PROMPT


def _get_last_qa(session_manager, session_id: str) -> tuple[str | None, str | None]:
    """获取当前会话的最后一轮 Q&A（连续 user → assistant 对）。"""
    session = session_manager.get(session_id)
    if session is None:
        return None, None

    # 优先从内存中的 conversation_history 读取
    history = getattr(session, "conversation_history", None) or []
    if not history:
        # 优先通过 SessionManager 读取，以复用版本迁移、校验和截断策略。
        loader = getattr(session_manager, "load_session_history", None)
        if callable(loader):
            try:
                history = loader(session_id) or []
            except Exception as error:
                _logger.debug("读取会话历史失败: %s", error)
        if not history:
            # 兼容只实现最小 get() 接口的第三方 SessionManager。
            files_path = getattr(session, "workspace_path", None) or getattr(
                session, "files_path", None
            )
            if files_path:
                hp = os.path.join(os.path.dirname(files_path), "history.json")
                if os.path.isfile(hp):
                    try:
                        with open(hp, encoding="utf-8-sig") as f:
                            raw_history = json.load(f)
                        history = (
                            raw_history.get("messages", [])
                            if isinstance(raw_history, dict)
                            else raw_history
                        )
                    except (OSError, ValueError, TypeError) as error:
                        _logger.debug("读取兼容历史文件失败: %s", error)
                        history = []

    assistant_idx = -1
    last_assistant: str | None = None
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            last_assistant = msg["content"]
            assistant_idx = i
            break

    if last_assistant is None:
        return None, None

    last_user: str | None = None
    for i in range(assistant_idx - 1, -1, -1):
        msg = history[i]
        if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("content"):
            last_user = msg["content"]
            break

    return last_user, last_assistant


def _review_writer(term_write: Any, capture: bool) -> Callable[[str, str], None]:
    """构造同时兼容 TUI transcript 与 stdout 的审查输出器。"""
    def write(text: str, color: str = "") -> None:
        if term_write and callable(term_write):
            try:
                term_write(_ANSI_COLOR_TO_STYLE.get(color, "class:cli-default"), text)
            except Exception as error:
                _logger.warning("_write 调用 term_write 失败: %s (text=%s)", error, text[:50])
        if not capture:
            print(text)

    return write


async def _iterate_review(
    user_msg: str,
    current_answer: str,
    issue_count: int,
    *,
    client: Any,
    max_iterations: int,
    write: Callable[[str, str], None],
) -> str:
    """迭代审查，直到通过、无改进或达到上限。"""
    from miniagent.agent.llm_json import llm_json

    for iteration in range(1, max_iterations):
        result = await llm_json(
            prompt=f"用户问题：\n{user_msg[:3000]}\n\n当前答案：\n{current_answer[:5000]}",
            system=_REVIEW_ITERATION_SYSTEM.replace("{prev_issue_count}", str(issue_count)),
            client=client,
        )
        if not result:
            write(f"{WARNING_PREFIX} 审查服务不可用，返回当前最佳答案", "ansired")
            break
        issues = result.get("issues", [])
        if not result.get("has_issues", False) or not issues:
            write(f"{SUCCESS_PREFIX} 第 {iteration + 1} 轮审查通过，无新问题。", "ansigreen")
            break
        issue_count = len(issues)
        improved = result.get("improved_answer")
        if not improved:
            write(
                f"{WARNING_PREFIX} 第 {iteration + 1} 轮发现 {len(issues)} 个问题，但无法生成改进答案",
                "ansired",
            )
            break
        current_answer = improved
        summary = "；".join(item.get("description", "")[:60] for item in issues[:2])
        write(
            f"🔄 第 {iteration + 1} 轮发现 {len(issues)} 个问题，继续改进：{summary}",
            "ansiyellow",
        )
    else:
        write(f"{WARNING_PREFIX} 已达到最大迭代次数（{max_iterations} 轮），返回最新答案", "ansiyellow")
    return current_answer


async def _run_review(
    user_msg: str,
    assistant_msg: str,
    *,
    extra_feedback: str = "",
    client: Any = None,
    term_write: Any = None,
    capture: bool = False,
    max_iterations: int = IMPROVE_MAX_ITERATIONS,
) -> str | None:
    """执行自我反驳式答案优化，并按调用渠道返回或打印最终答案。"""
    from miniagent.agent.llm_json import llm_json

    write = _review_writer(term_write, capture)
    write("🔍 正在审查答案…", "ansicyan")
    prompt_parts = [f"用户问题：\n{user_msg[:3000]}", f"\n当前答案：\n{assistant_msg[:5000]}"]
    if extra_feedback:
        prompt_parts.append(f"\n用户额外反馈：{extra_feedback}")
    result = await llm_json(prompt="\n".join(prompt_parts), system=_REVIEW_SYSTEM, client=client)
    if not result:
        write(f"{WARNING_PREFIX} 审查服务不可用（LLM 无响应）", "ansired")
        return None
    issues = result.get("issues", [])
    if not result.get("has_issues", False) or not issues:
        write(f"{SUCCESS_PREFIX} 未发现明显问题，答案质量良好。", "ansigreen")
        return None
    summary = "；".join(item.get("description", "")[:60] for item in issues[:3])
    write(f"{WARNING_PREFIX} 发现 {len(issues)} 个问题：{summary}", "ansiyellow")
    write("🔄 正在改进答案…", "ansicyan")
    current_answer = await _iterate_review(
        user_msg,
        result.get("improved_answer") or assistant_msg,
        len(issues),
        client=client,
        max_iterations=max_iterations,
        write=write,
    )
    write("\n--- 优化后的答案 ---", "ansigreen")
    if capture:
        return f"🔍 审查完成\n\n{current_answer[:2000]}"
    print(current_answer[:2000])
    return None


# ─── /improve 辅助函数 ───────────────────────────────

# IMPROVE_PROMPT 现在从 miniagent.agent.prompts.improver 导入
_IMPROVE_SYSTEM = IMPROVE_PROMPT

_IMPROVE_PROMPT = """请根据质量评估建议改进以下答案。

用户原始问题：
{user_input}

当前答案：
{current_answer}

质量评估建议：
{improve_suggestions}

改进要求：
1. 针对每条建议进行具体的改进
2. 保持答案的核心内容和结构
3. 补充遗漏的信息或细节
4. 提升答案的准确性和完整性
5. 优化表述的清晰度和专业性

返回 JSON 格式 {{\"improved_answer\": \"改进后的完整答案\"}}"""


async def _run_improve(
    user_msg: str,
    assistant_msg: str,
    suggestions: list[str],
    *,
    client: Any = None,
    term_write: Any = None,
    capture: bool = False,
) -> str | None:
    """执行答案改进（根据质量评估建议）。

    Args:
        user_msg: 用户原始问题
        assistant_msg: 当前答案
        suggestions: 改进建议列表
        client: OpenAI 异步客户端
        term_write: CLI transcript 写入回调（可选）
        capture: 是否捕获输出（飞书模式）

    Returns:
        改进后的答案（capture=True 时），或 None（直接 print）
    """
    from miniagent.agent.llm_json import llm_json

    def _write(text: str, color: str = "") -> None:
        """输出文本：优先走 term_write（全屏 CLI），无 capture 时 fallback 到 print。

        **注意**：term_write 实际是 cli_transcript_append，签名是 (style_cls, text)，
        不是 (text, color)。需要将 ANSI 颜色转换为样式类并调整参数顺序。
        """
        if term_write and callable(term_write):
            try:
                # 将 ANSI 颜色转换为样式类
                style_cls = _ANSI_COLOR_TO_STYLE.get(color, "class:cli-default")
                # cli_transcript_append 签名是 (style_cls, text)，需要反转参数
                term_write(style_cls, text)
            except Exception as e:
                _logger.warning("_write 调用 term_write 失败: %s (text=%s)", e, text[:50])
        if not capture:
            print(text)

    _write("🔄 正在根据建议改进答案…", "ansicyan")

    # 构建改进 prompt
    suggestions_text = "\n".join(f"- {s}" for s in suggestions)
    improve_prompt = _IMPROVE_PROMPT.format(
        user_input=user_msg[:3000],
        current_answer=assistant_msg[:5000],
        improve_suggestions=suggestions_text,
    )

    # 调用 LLM 生成改进答案（返回 JSON）
    result = await llm_json(
        prompt=improve_prompt,
        system=_IMPROVE_SYSTEM,
        client=client,
    )

    # 从 JSON 中提取改进答案
    improved_answer = result.get("improved_answer", "") if result else ""

    if not improved_answer:
        _write(f"{WARNING_PREFIX} 改进失败（LLM 无响应）", "ansired")
        return None

    _write(f"{SUCCESS_PREFIX} 答案已改进", "ansigreen")

    if capture:
        return f"🔄 改进完成\n\n{improved_answer[:2000]}"

    print(improved_answer)
    return improved_answer


def _format_status(state: CliLoopState | dict[str, Any]) -> str:
    """格式化 /status 输出。"""
    lines = []

    rt = state.get("runtime_ctx")
    if rt is None:
        return f"{WARNING_PREFIX} 运行时上下文未初始化（缺少 runtime_ctx）"

    message_queue = rt.message_queue
    channel_router = rt.channel_router
    feishu_rt = rt.feishu

    # 实例信息
    instance_id = state.get("instance_id")
    if instance_id:
        lines.append(f"🏭 实例: #{instance_id}")

    # 会话信息
    active = state.get("active_session_id", "")
    sm = state.get("session_manager")
    if sm and active:
        display = (
            sm.get_session_display_name(active)
            if hasattr(sm, "get_session_display_name")
            else active
        )
        lines.append(f"📁 当前会话: {display}")

    # 飞书状态
    feishu_on = feishu_rt.is_running()
    lines.append(f"💬 飞书: {'🟢 运行中' if feishu_on else '⚪ 未启用'}")

    # 通道绑定状态
    if channel_router is not None:
        bindings = channel_router.get_all_bindings()
        if bindings:
            lines.append(f"📡 通道绑定: {len(bindings)} 个通道已绑定")
            for ch, sess in bindings.items():
                lines.append(f"   {str(ch)[:20]} → {sess}")
        from miniagent.assistant.infrastructure.cli_feishu_policy import focus_mode_status_line

        focus = focus_mode_status_line(channel_router).strip()
        if focus:
            lines.append(focus)

    # 消息队列状态
    lines.append("")
    lines.append("📬 消息队列:")
    status = message_queue.get_status()
    mode_icon = "🟢" if status["mode"] == "queue" else "🔴"
    lines.append(f"  模式: {mode_icon} {status['mode']}")

    for label, info in status["chats"].items():
        if info["busy"]:
            elapsed = info.get("elapsed")
            elapsed_str = f" ({elapsed:.0f}s)" if elapsed else ""
            lines.append(f"  {label}: 🔴 处理中{elapsed_str}")
            if info["pending"] > 0:
                lines.append(f"    等待: {info['pending']} 条")
        else:
            lines.append(f"  {label}: ⚪ 空闲")

    return "\n".join(lines)


# ─── /test 辅助函数 ───────────────────────────────

async def _run_test(
    category: str | None = None,
    name_pattern: str | None = None,
    *,
    mock: bool = True,
    engine: Any = None,
    registry: Any = None,
    monitor: Any = None,
    skill_toolboxes: list | None = None,
    skill_prompts: str | None = None,
    state: dict[str, Any] | None = None,
    term_write: Any = None,
    capture: bool = False,
) -> str:
    """执行自测并返回结果。"""
    from miniagent.assistant.testing.agent_adapter import build_execute_agent_from_engine
    from miniagent.assistant.testing.test_runner import run_self_test

    def _write(text: str, color: str = "") -> None:
        """输出文本：优先走 term_write（全屏 CLI），无 capture 时 fallback 到 print。

        term_write 实际是 cli_transcript_append，签名是 (style_cls, text)。
        """
        if term_write and callable(term_write):
            try:
                style_cls = _ANSI_COLOR_TO_STYLE.get(color, "class:cli-default")
                term_write(style_cls, text)
            except Exception as e:
                _logger.warning("_write 调用 term_write 失败: %s (text=%s)", e, text[:50])
        if not capture:
            print(text)

    mode_label = "mock（样本校验）" if mock else "real（真实 Agent）"
    _write(f"🧪 正在运行自测 [{mode_label}]...", "ansicyan")

    execute_agent = None
    if not mock:
        if registry is None:
            msg = f"{WARNING_PREFIX} 真实模式需要 registry，请在 CLI 主循环中运行 /test run real"
            if capture:
                return msg
            _write(msg, "ansiyellow")
            return ""
        execute_agent = await build_execute_agent_from_engine(
            engine,
            registry=registry,
            monitor=monitor,
            skill_toolboxes=skill_toolboxes,
            skill_prompts=skill_prompts,
            state=state if isinstance(state, dict) else None,
        )

    report = await run_self_test(
        category=category,
        name_pattern=name_pattern,
        term_write=_write,
        execute_agent=execute_agent,
        mock=mock,
    )

    if capture:
        result_lines = [
            f"🧪 自测结果 [{mode_label}]：{report.passed}/{report.total} 通过 ({report.pass_rate:.1%})",
            f"失败: {report.failed}，跳过: {report.skipped}",
            f"执行时间：{report.duration_seconds:.1f}s",
        ]
        if report.failed > 0:
            result_lines.append("\n失败的测试：")
            for r in report.results:
                if not r.passed and not r.error_message.startswith("跳过:"):
                    result_lines.append(f"  ✗ {r.sample_name}: {r.error_message}")
        return "\n".join(result_lines)

    return ""


def _list_test_samples() -> str:
    """列出所有测试样本。"""
    from miniagent.assistant.testing.test_runner import TestRunner

    runner = TestRunner()
    samples = runner.load_samples()

    if not samples:
        return "📭 暂无测试样本"

    # 按类别分组
    by_category: dict[str, list] = {}
    for s in samples:
        if s.category not in by_category:
            by_category[s.category] = []
        by_category[s.category].append(s)

    lines = ["📋 测试样本列表:", ""]
    for cat, items in sorted(by_category.items()):
        lines.append(f"  [{cat}]")
        for s in items:
            desc = s.description[:40] if s.description else s.input[:40]
            priority_icon = "🔴" if s.priority == 1 else "🟡" if s.priority == 2 else "⚪"
            lines.append(f"    {priority_icon} {s.name}: {desc}")

    return "\n".join(lines)


def _get_test_status() -> str:
    """获取最近一次测试报告。"""
    from miniagent.assistant.testing.test_runner import TestRunner

    runner = TestRunner()
    report = runner.get_last_report()

    if not report:
        return "📭 暂无测试记录，请先运行 `/test run`"

    lines = [
        "🧪 最近测试报告：",
        f"  时间：{report.get('timestamp', '未知')}",
        f"  总数：{report.get('total', 0)}",
        f"  通过：{report.get('passed', 0)}",
        f"  失败：{report.get('failed', 0)}",
        f"  跳过：{report.get('skipped', 0)}",
        f"  通过率：{report.get('passed', 0) / max(1, report.get('total', 1)):.1%}",
        f"  执行时长：{report.get('duration_seconds', 0):.1f}s",
    ]

    return "\n".join(lines)


__all__ = ["dispatch_command"]
