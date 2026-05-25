"""Engine — 统一命令调度器

CLI 和飞书共享的命令路由，所有 `.命令` 都通过此模块处理。

核心特性：
- print 捕获：CLI 命令原本用 print()，飞书需要返回字符串
- 不中断：`.status` 等检查命令不会打断正在运行的 agent
- 远程约束：飞书侧 `capture=True` 时默认 `allow_session_mutations_when_capture=False`，
  阻止 `.session switch/create/rename` 与 `.schedule` 变异；`MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1` 时放开

命令全集与用户说明见 ``docs/CLI.md``；飞书约束见 ``docs/FEISHU.md``。
"""

from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


_REMOTE_SESSION_HINT = (
    "⚠️ 该命令会修改与 CLI 共享的会话状态，请在本地 MiniAgent 终端执行。\n"
    "飞书上可使用 .session list 查看会话列表。"
)


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
) -> str | None:
    """统一命令调度。

    Args:
        text: 用户输入的原始文本
        state: 运行时状态字典
        engine: UnifiedEngine 实例（agent 命令需要）
        registry: 工具注册表
        monitor: 性能监控器
        skill_toolboxes: 技能工具箱
        skill_prompts: 技能提示词
        capture: True = 捕获 print 输出并返回（飞书用），False = 直接 print（CLI 用）
        allow_session_mutations_when_capture: capture=True 时是否允许执行 .session
            switch/create/rename（飞书应传 False，避免改 CLI 共享 state）
        feishu_user_status: capture=True 时若传入，则 .feishu start 用其写入全屏 transcript；
            为 None 且 capture=True 时用 print 捕获（飞书）；capture=False 时默认
            使用 ``_feishu_user_status_fn(runtime_ctx)``
        message_queue_abort_chat_id: 飞书入站 ``chat_id``（集成侧应传入）；供 ``.queue abort`` / ``.abort``
            定位要中止的 per-chat 队列。缺省为 CLI 专用 ``__cli__``。

    Returns:
        capture=True 时返回输出字符串，capture=False 时返回 None
    """
    if not text.startswith("."):
        return None

    from miniagent.engine.cli_commands import (
        cmd_bind,
        cmd_help,
        cmd_instance_handler,
        cmd_queue_set,
        cmd_queue_status,
        cmd_session_create,
        cmd_session_list,
        cmd_session_rename,
        cmd_session_switch,
        cmd_unbind,
        feishu_dot_commands_full_enabled,
        feishu_markdown_commands_enabled,
        format_queue_abort_message,
        format_queue_command_usage,
        format_session_command_usage,
    )
    from miniagent.engine.session_lock import (
        is_session_locked,
        release_session_lock,
        try_lock_session,
    )

    rt = state.get("runtime_ctx")
    if rt is None:
        msg = "⚠️ 运行时上下文未初始化（缺少 runtime_ctx）"
        if capture:
            return msg
        print(msg)
        return None

    message_queue = rt.message_queue
    channel_router = rt.channel_router
    feishu_rt = rt.feishu
    md_cmds = capture and feishu_markdown_commands_enabled()
    allow_remote = allow_session_mutations_when_capture or feishu_dot_commands_full_enabled()
    block_remote = capture and not allow_remote

    parts = text.split()
    cmd = parts[0].lower() if parts else ""

    # ── .status ──
    if cmd == ".status":
        output = _format_status(state)
        if capture:
            return output
        print(output)
        return None

    # ── .stop：飞书 capture 默认拒绝；MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时与 CLI 相同
    if cmd == ".stop":
        if capture and not feishu_dot_commands_full_enabled():
            return "⚠️ .stop 命令只能在 CLI 使用（或设置 MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1）"
        from miniagent.engine.shutdown import shutdown_runtime

        await shutdown_runtime(
            rt,
            state,  # type: ignore[arg-type]
            reason="dot_stop_dispatch",
            release_cli_session_lock=True,
            call_unregister=True,
        )
        print("✅ 当前实例已停止")
        sys.exit(0)

    # ── .instance ──
    if cmd == ".instance":
        sub_cmd = parts[1] if len(parts) > 1 else ""
        output = _capture(
            lambda md=md_cmds: cmd_instance_handler(parts, sub_cmd, state, markdown=md)
        )
        if capture:
            return output
        print(output)
        return None

    # ── .session ──
    if cmd == ".session":
        sub_cmd = parts[1] if len(parts) > 1 else ""
        sm = state.get("session_manager")
        active = state.get("active_session_id", "")

        # 飞书 capture 默认只读 list；FULL=1 或 allow_remote 时与 CLI 相同
        if sub_cmd == "list":
            output = _capture(lambda md=md_cmds: cmd_session_list(sm, active, markdown=md))
        elif sub_cmd == "switch" and len(parts) >= 3:
            if block_remote:
                output = _REMOTE_SESSION_HINT
            else:
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf):
                        new_active = await cmd_session_switch(
                            sm,
                            active,
                            parts[2],
                            try_lock_session,
                            release_session_lock,
                            is_session_locked,
                            channel_router,
                            state.get("feishu_p2p_synced_senders")
                            if isinstance(state.get("feishu_p2p_synced_senders"), set)
                            else None,
                        )
                    state["active_session_id"] = new_active
                    output = buf.getvalue().strip()
                except Exception as e:
                    output = f"❌ 命令执行失败: {e}"
        elif sub_cmd == "create" and len(parts) >= 3:
            if block_remote:
                output = _REMOTE_SESSION_HINT
            else:
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf):
                        await cmd_session_create(
                            sm,
                            parts[2],
                            parts[3] if len(parts) > 3 else None,
                            try_lock_session,
                        )
                    output = buf.getvalue().strip()
                except Exception as e:
                    output = f"❌ 命令执行失败: {e}"
        elif sub_cmd == "rename" and len(parts) >= 4:
            if block_remote:
                output = _REMOTE_SESSION_HINT
            else:
                output = _capture(lambda: cmd_session_rename(sm, parts[2], " ".join(parts[3:])))
        else:
            output = format_session_command_usage()

        if capture:
            return output
        print(output)
        return None

    # ── .feishu ──
    if cmd == ".feishu":
        factory = rt.create_feishu_handler_factory

        def _resolve_feishu_user_status() -> Callable[[str], None] | None:
            if feishu_user_status is not None:
                return feishu_user_status
            if capture:
                return None
            from miniagent.engine.main import _feishu_user_status_fn

            return _feishu_user_status_fn(rt)

        if text == ".feishu start":
            if factory is None:
                output = "⚠️ 飞书处理器工厂未初始化"
            else:
                us = _resolve_feishu_user_status()

                def _start() -> None:
                    from miniagent.skills.snapshots import (
                        get_skill_prompts_from_state,
                        get_skill_toolboxes_from_state,
                    )

                    feishu_rt.start(
                        get_skill_toolboxes_from_state(state) or skill_toolboxes or [],
                        get_skill_prompts_from_state(state) or skill_prompts or [],
                        factory,
                        state,
                        user_status=us,
                    )

                output = _capture(_start)
        elif text == ".feishu stop":
            output = _capture(feishu_rt.stop)
        else:
            output = _capture(feishu_rt.status)

        if capture:
            return output
        print(output)
        return None

    def _abort_queue_output() -> str:
        """中止当前/指定 chat 队列上的 dispatch 任务并返回格式化结果文本。"""
        tid = (message_queue_abort_chat_id or "").strip() or message_queue.CLI_CHAT_ID
        res = message_queue.abort_chat(tid)
        return format_queue_abort_message(res)

    # ── .abort / .queue abort（不退出进程；飞书 handler 应传入当前 chat_id，缺省视为 CLI 队列）──
    if cmd == ".abort":
        output = _abort_queue_output()
        if capture:
            return output
        print(output)
        return None

    # ── .queue ──
    if cmd == ".queue":
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "status":
            output = _capture(lambda md=md_cmds: cmd_queue_status(message_queue, markdown=md))
        elif sub == "set" and len(parts) >= 3:
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    await cmd_queue_set(message_queue, parts[2])
                output = buf.getvalue().strip()
            except Exception as e:
                output = f"❌ 命令执行失败: {e}"
        elif sub == "abort":
            output = _abort_queue_output()
        else:
            output = format_queue_command_usage(message_queue)

        if capture:
            return output
        print(output)
        return None

    # ── .stats ──
    if cmd == ".stats":
        if monitor:
            output = _capture(lambda: print(f"\n{monitor.report()}"))
        else:
            output = "⚠️ 监控器未初始化"
        if capture:
            return output
        print(output)
        return None

    # ── .reload-skills ──
    if cmd in (".reload-skills", ".reload_skills"):
        try:
            from miniagent.skills.refresh import refresh_skills

            fr = await refresh_skills(
                registry,
                rt.skill_registry,
                state=state,
                session_manager=state.get("session_manager"),
            )
            output = (
                f"🔄 技能已重新加载\n"
                f"  包: {', '.join(fr.package_ids) or '(无)'}\n"
                f"  技能数: {len(fr.loaded_skills)}\n"
                f"  新增工具: {len(fr.added_tools)}\n"
                f"  移除工具: {len(fr.removed_tools)}"
            )
        except Exception as e:
            output = f"❌ 技能 reload 失败: {e}"
        if capture:
            return output
        print(output)
        return None

    # ── .help ──
    if cmd == ".help":
        output = _capture(
            lambda: cmd_help(message_queue, state.get("instance_id"))
        )
        if capture:
            return output
        print(output)
        return None

    # ── .bind ──
    if cmd == ".bind":
        if channel_router is None:
            output = "⚠️ 通道路由器未初始化"
        else:
            args = parts[1:] if len(parts) > 1 else []
            output = cmd_bind(channel_router, args, state)
        if capture:
            return output
        print(output)
        return None

    # ── .unbind ──
    if cmd == ".unbind":
        if channel_router is None:
            output = "⚠️ 通道路由器未初始化"
        else:
            args = parts[1:] if len(parts) > 1 else []
            output = cmd_unbind(channel_router, args, state)
        if capture:
            return output
        print(output)
        return None

    # ── .schedule（定时任务）──
    if cmd == ".schedule":
        from miniagent.engine.cli_commands import cmd_schedule

        sub_s = parts[1].lower() if len(parts) > 1 else ""
        mutating = sub_s in ("add", "remove", "enable", "disable")
        allow_muts = not (block_remote and mutating)
        output = cmd_schedule(text, allow_mutations=allow_muts)
        if capture:
            return output
        print(output)
        return None

    # ── .review（自我反驳式答案优化）──
    if cmd == ".review":
        rt = state.get("runtime_ctx")
        sm = state.get("session_manager")
        session_id = state.get("active_session_id", "")
        if rt is None or sm is None or not session_id:
            output = "⚠️ .review 需要会话上下文和会话管理器"
        else:
            # 获取最后一轮 Q&A
            user_msg, assistant_msg = _get_last_qa(sm, session_id)
            if not user_msg or not assistant_msg:
                output = "⚠️ 当前会话无历史对话，无法审查"
            else:
                extra_feedback = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
                output = await _run_review(
                    user_msg, assistant_msg,
                    extra_feedback=extra_feedback,
                    client=getattr(rt, "openai_client", None),
                    term_write=getattr(rt, "cli_transcript_append", None),
                    capture=capture,
                )
        if capture:
            return output
        if output:
            print(output)
        return None

    # ── .confirm / .adjust / .reject（确认侧通道）──
    if cmd in (".confirm", ".adjust", ".reject"):
        cc = getattr(engine, "confirmation_channel", None) if engine else None
        if cc is None or not cc.has_pending:
            output = "⚠️ 当前无待确认的请求"
        elif cmd == ".confirm":
            from miniagent.types.confirmation import ConfirmationResult

            cc.respond(ConfirmationResult(approved=True))
            output = "✅ 已确认，继续执行"
        elif cmd == ".reject":
            from miniagent.types.confirmation import ConfirmationResult

            cc.respond(ConfirmationResult(approved=False, rejected=True))
            output = "⚠️ 已拒绝，取消当前操作"
        else:
            # .adjust <新内容>
            adjustment = " ".join(parts[1:]).strip()
            if not adjustment:
                output = "用法：.adjust <调整后的内容>"
            else:
                from miniagent.types.confirmation import ConfirmationResult

                cc.respond(ConfirmationResult(approved=True, adjustment=adjustment))
                output = f"✅ 已调整并确认：{adjustment[:60]}{'…' if len(adjustment) > 60 else ''}"

        if capture:
            return output
        print(output)
        return None

    # 不是已知命令，返回 None 让调用者交给 agent 处理
    return None


def _capture(fn) -> str:
    """捕获 print 输出并返回字符串。"""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            fn()
    except Exception as e:
        return f"❌ 命令执行失败: {e}"
    return buf.getvalue().strip()


# ─── .review 辅助函数 ───────────────────────────────

_REVIEW_SYSTEM = """你是一个严格的自我批判专家。请审查以下问答中的知识错误、逻辑错误、表述不清等。

审查要求：
1. 检查事实性错误（知识错误）
2. 检查逻辑谬误（因果倒置、循环论证等）
3. 检查遗漏的关键信息
4. 检查表述是否清晰准确
5. 检查是否有更好的表达方式

如果没有任何问题，返回 {"has_issues": false, "issues": [], "improved_answer": null}

如果发现问题，返回：
{"has_issues": true, "issues": [{"type": "knowledge_error|logic_error|clarity|omission", "description": "具体描述"}], "improved_answer": "改进后的完整答案"}

只返回 JSON，不要其他文字。"""

_REVIEW_ITERATION_SYSTEM = """你是一个严格的自我批判专家。以下是一份经过一轮审查的答案，请再次检查是否还有遗漏的问题。

特别注意：上次审查发现的 {prev_issue_count} 个问题应该已经修复，请确认是否确实修复，并查找其他可能的问题。

审查要求同上。如果没有任何问题，返回 {"has_issues": false, "issues": [], "improved_answer": null}。

只返回 JSON，不要其他文字。"""


def _get_last_qa(session_manager, session_id: str) -> tuple[str | None, str | None]:
    """获取当前会话的最后一轮 Q&A。"""
    session = session_manager.get(session_id)
    if session is None:
        return None, None

    # 优先从内存中的 conversation_history 读取
    history = getattr(session, "conversation_history", None) or []
    if not history:
        # 回退到 history.json
        files_path = getattr(session, "workspace_path", None) or getattr(session, "files_path", None)
        if files_path:
            hp = os.path.join(os.path.dirname(files_path), "history.json")
            if os.path.isfile(hp):
                try:
                    with open(hp, encoding="utf-8-sig") as f:
                        history = json.load(f)
                except Exception:
                    history = []

    last_user = None
    last_assistant = None
    for msg in reversed(history):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and content and last_user is None:
                last_user = content
            elif role == "assistant" and content and last_assistant is None:
                last_assistant = content
            if last_user and last_assistant:
                break
    return last_user, last_assistant


async def _run_review(
    user_msg: str,
    assistant_msg: str,
    *,
    extra_feedback: str = "",
    client: Any = None,
    term_write: Any = None,
    capture: bool = False,
    max_iterations: int = 3,
) -> str | None:
    """执行自我反驳式答案优化。

    Args:
        user_msg: 用户原始问题
        assistant_msg: 当前答案
        extra_feedback: 用户附加反馈（如 "代码太复杂"）
        client: OpenAI 异步客户端
        term_write: CLI transcript 写入回调（可选）
        capture: 是否捕获输出（飞书模式）
        max_iterations: 最大迭代轮数

    Returns:
        最终输出字符串（capture=True 时），或 None（直接 print）
    """
    from miniagent.core.llm_json import llm_json

    def _write(text: str, color: str = "") -> None:
        if term_write and callable(term_write):
            try:
                term_write(text, color)
            except Exception:
                pass
        if not capture:
            print(text)

    _write("🔍 正在审查答案…", "ansicyan")

    # 第一轮审查
    prompt_parts = [
        f"用户问题：\n{user_msg[:3000]}",
        f"\n当前答案：\n{assistant_msg[:5000]}",
    ]
    if extra_feedback:
        prompt_parts.append(f"\n用户额外反馈：{extra_feedback}")

    review_result = await llm_json(
        prompt="\n".join(prompt_parts),
        system=_REVIEW_SYSTEM,
        client=client,
    )

    # 空结果说明 LLM 调用失败或无响应
    if not review_result:
        _write("⚠️ 审查服务不可用（LLM 无响应）", "ansired")
        return None

    has_issues = review_result.get("has_issues", False)
    issues = review_result.get("issues", [])
    improved = review_result.get("improved_answer")

    if not has_issues or not issues:
        _write("✅ 未发现明显问题，答案质量良好。", "ansigreen")
        return None

    issue_summary = "；".join(i.get("description", "")[:60] for i in issues[:3])
    _write(f"⚠️ 发现 {len(issues)} 个问题：{issue_summary}", "ansiyellow")
    _write("🔄 正在改进答案…", "ansicyan")

    current_answer = improved or assistant_msg
    prev_issue_count = len(issues)

    # 迭代改进
    for iteration in range(1, max_iterations):
        review_result = await llm_json(
            prompt=f"用户问题：\n{user_msg[:3000]}\n\n当前答案：\n{current_answer[:5000]}",
            system=_REVIEW_ITERATION_SYSTEM.format(prev_issue_count=prev_issue_count),
            client=client,
        )

        # 空结果 → LLM 不可用
        if not review_result:
            _write("⚠️ 审查服务不可用，返回当前最佳答案", "ansired")
            break

        has_issues = review_result.get("has_issues", False)
        new_issues = review_result.get("issues", [])
        new_improved = review_result.get("improved_answer")

        if not has_issues or not new_issues:
            _write(f"✅ 第 {iteration + 1} 轮审查通过，无新问题。", "ansigreen")
            break

        prev_issue_count = len(new_issues)
        if new_improved:
            current_answer = new_improved
            issue_summary = "；".join(i.get("description", "")[:60] for i in new_issues[:2])
            _write(f"🔄 第 {iteration + 1} 轮发现 {len(new_issues)} 个问题，继续改进：{issue_summary}", "ansiyellow")
        else:
            _write(f"⚠️ 第 {iteration + 1} 轮发现 {len(new_issues)} 个问题，但无法生成改进答案", "ansired")
            break
    else:
        _write(f"⚠️ 已达到最大迭代次数（{max_iterations} 轮），返回最新答案", "ansiyellow")

    # 输出最终答案
    _write("\n--- 优化后的答案 ---", "ansigreen")
    if capture:
        return f"🔍 审查完成\n\n{current_answer[:2000]}"
    print(current_answer[:2000])
    return None


def _format_status(state: CliLoopState | dict[str, Any]) -> str:
    """格式化 .status 输出。"""
    lines = []

    rt = state.get("runtime_ctx")
    if rt is None:
        return "⚠️ 运行时上下文未初始化（缺少 runtime_ctx）"

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


__all__ = ["dispatch_command"]
