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

from miniagent.core.prompts.improver import IMPROVE_PROMPT
from miniagent.core.prompts.reviewer import REVIEW_ITERATION_PROMPT, REVIEW_PROMPT
from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.logger import get_logger
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX

_logger = get_logger(__name__)


# ─── 已注册命令列表（用于模糊匹配与 CLI 补全）────────────────────────────────
# 顺序影响 _find_command_by_prefix：同前缀时返回列表中先出现的项
# （例如 "/sta" 会匹配 "/stats" 而非 "/status"，因 "/stats" 更靠前）。
_REGISTERED_COMMANDS = [
    "/help",
    "/session",
    "/instance",
    "/feishu",
    "/queue",
    "/abort",
    "/query",
    "/btw",
    "/schedule",
    "/self-opt",  # 新增：自我优化命令
    "/kb",
    "/model",
    "/config",
    "/doctor",
    "/stats",
    "/status",
    "/stop",
    "/confirm",
    "/adjust",
    "/reject",
    "/review",
    "/improve",
    "/test",
    "/reload-skills",
    "/reload-config",  # 新增：配置热更新命令
]


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

# 旧版 ``.reload-skills`` 别名 → 统一 ``/`` 前缀
_LEGACY_COMMAND_ALIASES: dict[str, str] = {
    ".reload-skills": "/reload-skills",
    ".reload_skills": "/reload-skills",
}


def _normalize_command_text(text: str) -> str | None:
    """规范化命令文本；非命令输入返回 None。"""
    stripped = text.strip()
    if not stripped:
        return None
    parts = stripped.split()
    first = parts[0].lower()
    if first in _LEGACY_COMMAND_ALIASES:
        normalized = _LEGACY_COMMAND_ALIASES[first]
        if len(parts) > 1:
            normalized += " " + " ".join(parts[1:])
        return normalized
    if stripped.startswith("/"):
        return stripped
    return None


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
        allow_session_mutations_when_capture: capture=True 时是否允许执行 /session
            switch/create/rename（飞书应传 False，避免改 CLI 共享 state）
        feishu_user_status: capture=True 时若传入，则 /feishu start 用其写入全屏 transcript；
            为 None 且 capture=True 时用 print 捕获（飞书）；capture=False 时默认
            使用 ``_feishu_user_status_fn(runtime_ctx)``
        message_queue_abort_chat_id: 飞书入站 ``chat_id``（集成侧应传入）；供 ``/queue abort`` / ``/abort``
            定位要中止的 per-chat 队列。缺省为 CLI 专用 ``__cli__``。
        confirmation_session_key: 飞书入站时传入的 ``session_key``，供 ``/confirm`` 等确认命令路由。

    Returns:
        capture=True 时返回输出字符串（无文本时可能为 ``""``，表示命令已处理），
        capture=False 时返回 None；``/stop`` 成功时返回 ``"__EXIT__"``。
    """
    normalized = _normalize_command_text(text)
    if normalized is None:
        return None
    text = normalized

    from miniagent.engine.btw_cmd import (
        cmd_btw_cancel,
        cmd_btw_clear,
        cmd_btw_result,
        cmd_btw_start,
        cmd_btw_status,
    )
    from miniagent.engine.cli_commands import (
        cmd_help,
        cmd_instance_handler,
        cmd_kb_list,
        cmd_kb_mount,
        cmd_kb_reload,
        cmd_kb_search,
        cmd_kb_unmount,
        cmd_queue_set,
        cmd_queue_status,
        cmd_self_opt_analyze,
        cmd_self_opt_apply,
        cmd_self_opt_approve,
        cmd_self_opt_proposals,
        cmd_self_opt_reject,
        cmd_self_opt_report,
        cmd_self_opt_show,
        # 自我优化命令
        cmd_self_opt_status,
        cmd_session_create,
        cmd_session_delete,
        cmd_session_list,
        cmd_session_rename,
        cmd_session_switch,
        feishu_dot_commands_full_enabled,
        feishu_markdown_commands_enabled,
        format_kb_command_usage,
        format_queue_abort_message,
        format_queue_command_usage,
        format_session_command_usage,
        format_test_command_usage,
    )
    from miniagent.engine.doctor import diagnose_environment
    from miniagent.engine.model_cmd import format_model_info, switch_model
    from miniagent.engine.session_lock import (
        is_session_locked,
        release_session_lock,
        try_lock_session_async,
    )

    rt = state.get("runtime_ctx")
    if rt is None:
        msg = f"{WARNING_PREFIX} 运行时上下文未初始化（缺少 runtime_ctx）"
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

    # ── /status ──
    if cmd == "/status":
        output = _format_status(state)
        if capture:
            return output
        print(output)
        return None

    # ── /stop：飞书 capture 默认拒绝；feishu.dot_commands_full=true 时与 CLI 相同
    if cmd == "/stop":
        if capture and not feishu_dot_commands_full_enabled():
            return f"{WARNING_PREFIX} /stop 命令只能在 CLI 使用（或设置 feishu.dot_commands_full=true）"
        from miniagent.engine.shutdown import shutdown_runtime

        await shutdown_runtime(
            rt,
            state,  # type: ignore[arg-type]
            reason="dot_stop_dispatch",
            release_cli_session_lock=True,
            call_unregister=True,
        )
        return "__EXIT__"

    # ── instance ──
    if cmd == "/instance":
        sub_cmd = parts[1] if len(parts) > 1 else ""
        output = _capture(
            lambda md=md_cmds: cmd_instance_handler(parts, sub_cmd, state, markdown=md)
        )
        if capture:
            return output
        print(output)
        return None

    # ── session ──
    if cmd == "/session":
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
                            try_lock_session_async,
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
                    output = f"{ERROR_PREFIX} 命令执行失败: {e}"
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
                            try_lock_session_async,
                        )
                    output = buf.getvalue().strip()
                except Exception as e:
                    output = f"{ERROR_PREFIX} 命令执行失败: {e}"
        elif sub_cmd == "rename" and len(parts) >= 4:
            if block_remote:
                output = _REMOTE_SESSION_HINT
            else:
                output = _capture(lambda: cmd_session_rename(sm, parts[2], " ".join(parts[3:])))
        elif sub_cmd == "delete" and len(parts) >= 3:
            if block_remote:
                output = _REMOTE_SESSION_HINT
            else:
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf):
                        cmd_session_delete(
                            sm,
                            active,
                            parts[2],
                            release_session_lock,
                        )
                    output = buf.getvalue().strip()
                except Exception as e:
                    output = f"{ERROR_PREFIX} 命令执行失败: {e}"
        else:
            output = format_session_command_usage()

        if capture:
            return output
        print(output)
        return None

    # ── /feishu ──
    if cmd == "/feishu":
        factory = rt.create_feishu_handler_factory

        def _resolve_feishu_user_status() -> Callable[[str], None] | None:
            """解析飞书状态行回调：优先使用传入参数，capture 模式下返回 None。"""
            if feishu_user_status is not None:
                return feishu_user_status
            if capture:
                return None
            from miniagent.engine.utils import feishu_user_status_fn

            return feishu_user_status_fn(rt)

        if text == "/feishu start":
            if factory is None:
                output = f"{WARNING_PREFIX} 飞书处理器工厂未初始化"
            else:
                us = _resolve_feishu_user_status()

                def _start() -> None:
                    """启动飞书长轮询任务。"""
                    feishu_rt.start(factory, state, user_status=us)

                output = _capture(_start)
        elif text == "/feishu stop":
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    await feishu_rt.stop_async()
            except Exception as e:
                output = f"{ERROR_PREFIX} 命令执行失败: {e}"
            else:
                output = buf.getvalue().strip()
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

    # ── /abort / /queue abort（不退出进程；飞书 handler 应传入当前 chat_id，缺省视为 CLI 队列）──
    if cmd == "/abort":
        output = _abort_queue_output()
        if capture:
            return output
        print(output)
        return None

    # ── /queue ──
    if cmd == "/queue":
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
                output = f"{ERROR_PREFIX} 命令执行失败: {e}"
        elif sub == "abort":
            output = _abort_queue_output()
        else:
            output = format_queue_command_usage(message_queue)

        if capture:
            return output
        print(output)
        return None

    # ── /stats ──
    if cmd == "/stats":
        if monitor:
            output = _capture(lambda: print(f"\n{monitor.report()}"))
        else:
            output = f"{WARNING_PREFIX} 监控器未初始化"
        if capture:
            return output
        print(output)
        return None

    # ── /reload-skills（兼容旧 .reload-skills）──
    if cmd in ("/reload-skills", ".reload-skills", ".reload_skills"):
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
            output = f"{ERROR_PREFIX} 技能 reload 失败: {e}"
        if capture:
            return output
        print(output)
        return None

    # ── btw: 后台任务系统 ──
    if cmd == "/btw":
        sub_cmd = parts[1] if len(parts) > 1 else ""

        if sub_cmd == "start" and len(parts) >= 3:
            # 启动后台任务：/btw start <prompt>
            prompt = " ".join(parts[2:])
            output = await cmd_btw_start(engine, prompt, state)
        elif sub_cmd == "status":
            # 查看状态：/btw status [task_id]
            task_id = parts[2] if len(parts) >= 3 else None
            output = cmd_btw_status(task_id)
        elif sub_cmd == "result" and len(parts) >= 3:
            # 获取结果：/btw result <task_id>
            output = await cmd_btw_result(parts[2])
        elif sub_cmd == "cancel" and len(parts) >= 3:
            # 取消任务：/btw cancel <task_id>
            output = await cmd_btw_cancel(parts[2])
        elif sub_cmd == "clear":
            # 清理任务：/btw clear
            output = cmd_btw_clear()
        else:
            # 默认显示帮助和任务列表
            output = cmd_btw_status()  # 显示所有任务

        if capture:
            return output
        print(output)
        return None

    # ── model: 显示/切换模型 ──
    if cmd == "/model":
        if len(parts) > 1:
            # 切换模型
            new_model = parts[1]
            output = switch_model(new_model)
        else:
            # 显示当前模型
            output = format_model_info()
        if capture:
            return output
        print(output)
        return None

    # ── doctor: 环境诊断 ──
    if cmd == "/doctor":
        output = diagnose_environment()
        if capture:
            return output
        print(output)
        return None

    # ── query: 队列状态（合并/queue status） ──
    if cmd == "/query":
        output = _capture(lambda md=md_cmds: cmd_queue_status(message_queue, markdown=md))
        if capture:
            return output
        print(output)
        return None

    # ── /config（配置查看）──
    if cmd == "/config":
        from miniagent.engine.config_cmd import format_config_info

        section = parts[1] if len(parts) > 1 else None
        output = format_config_info(section)
        if capture:
            return output
        print(output)
        return None

    # ── /help ──
    if cmd == "/help":
        output = _capture(
            lambda: cmd_help(message_queue, state.get("instance_id"))
        )
        if capture:
            return output
        print(output)
        return None

    # ── /schedule（定时任务）──
    if cmd == "/schedule":
        from miniagent.engine.cli_commands import cmd_schedule

        sub_s = parts[1].lower() if len(parts) > 1 else ""
        mutating = sub_s in ("add", "remove", "enable", "disable")
        allow_muts = not (block_remote and mutating)
        output = cmd_schedule(text, allow_mutations=allow_muts)
        if capture:
            return output
        print(output)
        return None

    # ── /self-opt（自我优化）──
    if cmd == "/self-opt":
        from miniagent.core.constants import CLI_SELF_OPT_TOOLS
        from miniagent.infrastructure.json_config import get_config

        if not CLI_SELF_OPT_TOOLS or not get_config("self_optimization.enabled", True):
            msg = f"{WARNING_PREFIX} 自我优化功能已关闭（self_optimization.enabled）"
            if capture:
                return msg
            print(msg)
            return None

        sub_cmd = parts[1].lower() if len(parts) > 1 else ""

        # self-opt 不经过消息队列；输出须走 _capture，供全屏 CLI / 飞书 capture 路径消费
        if sub_cmd in ("status", ""):
            output = _capture(cmd_self_opt_status)
        elif sub_cmd == "proposals":
            status_filter = parts[2] if len(parts) > 2 else None
            output = _capture(lambda: cmd_self_opt_proposals(status=status_filter))
        elif sub_cmd == "show":
            if len(parts) >= 3:
                output = _capture(lambda: cmd_self_opt_show(parts[2]))
            else:
                output = "用法: /self-opt show <id>"
        elif sub_cmd == "approve":
            if len(parts) >= 3:
                output = _capture(lambda: cmd_self_opt_approve(parts[2]))
            else:
                output = "用法: /self-opt approve <id>"
        elif sub_cmd == "reject":
            if len(parts) >= 3:
                output = _capture(lambda: cmd_self_opt_reject(parts[2]))
            else:
                output = "用法: /self-opt reject <id>"
        elif sub_cmd == "apply":
            if len(parts) >= 3:
                proposal_id = parts[2]
                root = parts[3] if len(parts) > 3 else ""
                buf = io.StringIO()
                try:
                    with redirect_stdout(buf):
                        await cmd_self_opt_apply(proposal_id, root=root)
                    output = buf.getvalue().strip()
                except Exception as e:
                    output = f"{ERROR_PREFIX} 命令执行失败: {e}"
            else:
                output = "用法: /self-opt apply <id> [root]"
        elif sub_cmd == "analyze":
            output = _capture(cmd_self_opt_analyze)
        elif sub_cmd == "report":
            date = parts[2] if len(parts) > 2 else None
            output = _capture(lambda: cmd_self_opt_report(date=date))
        else:
            output = (
                f"{WARNING_PREFIX} 未知的子命令: {sub_cmd}\n"
                "用法: /self-opt status|proposals|show|approve|reject|apply|analyze|report"
            )

        if capture:
            return output
        print(output)
        return None

    # ── /kb（知识库）──
    if cmd == "/kb":
        sub_cmd = parts[1].lower() if len(parts) > 1 else ""
        md_kb = capture and feishu_markdown_commands_enabled()

        if sub_cmd in ("list", ""):
            output = _capture(lambda md=md_kb: cmd_kb_list(markdown=md))
        elif sub_cmd == "mount" and len(parts) >= 3:
            path = parts[2]
            name = parts[3] if len(parts) > 3 else None
            output = _capture(lambda: cmd_kb_mount(path, name))
        elif sub_cmd == "unmount" and len(parts) >= 3:
            output = _capture(lambda: cmd_kb_unmount(parts[2]))
        elif sub_cmd == "search" and len(parts) >= 3:
            query = " ".join(parts[2:])
            kb_name = None
            # 检查是否指定了知识库名称（最后一个参数如果是知识库名称）
            from miniagent.knowledge import get_kb_registry
            kb_registry = get_kb_registry()
            kb_list = kb_registry.list()
            kb_names = [kb["name"] for kb in kb_list]
            if len(parts) >= 4 and parts[-1] in kb_names:
                kb_name = parts[-1]
                query = " ".join(parts[2:-1])
            output = _capture(lambda: cmd_kb_search(query, kb_name))
        elif sub_cmd == "reload":
            name = parts[2] if len(parts) > 2 else None
            output = _capture(lambda: cmd_kb_reload(name))
        else:
            output = format_kb_command_usage()

        if capture:
            return output
        print(output)
        return None

    # ── /review（自我反驳式答案优化）──
    if cmd == "/review":
        rt = state.get("runtime_ctx")
        sm = state.get("session_manager")
        session_id = state.get("active_session_id", "")
        if rt is None or sm is None or not session_id:
            output = f"{WARNING_PREFIX} /review 需要会话上下文和会话管理器"
        else:
            # 获取最后一轮 Q&A
            user_msg, assistant_msg = _get_last_qa(sm, session_id)
            if not user_msg or not assistant_msg:
                output = f"{WARNING_PREFIX} 当前会话无历史对话，无法审查"
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
            # 进度已由 term_write 写入 transcript；空串表示已处理，避免 fallthrough
            return output if output is not None else ""
        if output:
            print(output)
        return None

    # ── /improve（根据质量评估建议改进答案）──
    if cmd == "/improve":
        rt = state.get("runtime_ctx")
        sm = state.get("session_manager")
        session_id = state.get("active_session_id", "")
        if rt is None or sm is None or not session_id:
            output = f"{WARNING_PREFIX} /improve 需要会话上下文和会话管理器"
        else:
            # 解析参数
            force = "--force" in parts
            reset = "--reset" in parts

            # 导入辅助函数
            from miniagent.engine.cli_commands import cmd_improve

            result = cmd_improve(sm, session_id, force=force, reset=reset)

            if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], bool):
                # 错误情况
                output = result[0]
            else:
                # 执行改进
                user_msg_dict, assistant_msg_dict, suggestions = result
                user_msg = user_msg_dict.get("content", "")
                assistant_msg = assistant_msg_dict.get("content", "")

                improved_answer = await _run_improve(
                    user_msg, assistant_msg, suggestions,
                    client=getattr(rt, "openai_client", None),
                    term_write=getattr(rt, "cli_transcript_append", None),
                    capture=capture,
                )

                if improved_answer:
                    # 追加到历史
                    session = sm.get(session_id)
                    if session:
                        metadata = assistant_msg_dict.get("metadata", {})
                        improve_round = metadata.get("improve_round", 0) + 1 if metadata.get("improved") else 1

                        session.conversation_history.append({
                            "role": "assistant",
                            "content": improved_answer,
                            "metadata": {
                                "improved": True,
                                "improve_round": improve_round,
                            }
                        })
                        # 持久化
                        sm.save_session_history(session_id)

                    output = improved_answer if capture else None
                else:
                    output = f"{WARNING_PREFIX} 改进失败"

        if capture:
            return output
        if output:
            print(output)
        return None

    # ── /confirm / /adjust / /reject（确认侧通道）──
    if cmd in ("/confirm", "/adjust", "/reject"):
        from miniagent.engine.parallel_config import resolve_active_session_key

        sk = confirmation_session_key or resolve_active_session_key(
            channel_router, state.get("active_session_id") or "default"
        )
        cc = None
        if engine is not None:
            engine.set_active_session_key(sk)
            cc = engine.get_confirmation_channel(sk)
        if cc is None or not cc.has_pending:
            output = f"{WARNING_PREFIX} 当前无待确认的请求"
        elif cmd == "/confirm":
            from miniagent.types.confirmation import ConfirmationResult

            cc.respond(ConfirmationResult.confirm())
            output = f"{SUCCESS_PREFIX} 已确认，继续执行"
        elif cmd == "/reject":
            from miniagent.types.confirmation import ConfirmationResult

            cc.respond(ConfirmationResult.reject())
            output = f"{WARNING_PREFIX} 已拒绝，取消当前操作"
        else:
            # /adjust <新内容>
            adjustment = " ".join(parts[1:]).strip()
            if not adjustment:
                from miniagent.types.confirmation import ConfirmationStage

                output = "用法：/adjust <调整后的内容>"
                pending = cc.pending
                if pending and pending.stage == ConfirmationStage.PLAN:
                    ref = (pending.full_content or pending.content or "").strip()
                    if ref:
                        preview = ref if len(ref) <= 2000 else f"{ref[:2000]}…"
                        output = f"{output}\n\n当前完整计划：\n{preview}"
            else:
                from miniagent.types.confirmation import ConfirmationResult

                cc.respond(ConfirmationResult.adjust(adjustment))
                output = f"{SUCCESS_PREFIX} 已调整并确认：{adjustment[:60]}{'…' if len(adjustment) > 60 else ''}"

        if capture:
            return output
        print(output)
        return None

    # ── /test（自测命令）──
    if cmd == "/test":
        sub_cmd = parts[1].lower() if len(parts) > 1 else ""

        if sub_cmd == "run":
            # 运行测试
            category_filter = parts[2] if len(parts) > 2 else None
            name_pattern = parts[3] if len(parts) > 3 else None

            output = await _run_test(
                category=category_filter,
                name_pattern=name_pattern,
                term_write=getattr(rt, "cli_transcript_append", None),
                capture=capture,
            )
        elif sub_cmd == "list":
            # 列出测试样本
            output = _list_test_samples()
        elif sub_cmd == "status":
            # 查看最近测试结果
            output = _get_test_status()
        else:
            # 显示用法
            output = format_test_command_usage()

        if capture:
            return output
        print(output)
        return None

    # ── /reload-config（配置热更新）──
    if cmd == "/reload-config":
        from miniagent.infrastructure.json_config import reload_runtime_config

        try:
            reload_runtime_config()
            output = f"{SUCCESS_PREFIX} 配置已重新加载"
        except Exception as e:
            output = f"{ERROR_PREFIX} 配置加载失败: {e}"

        if capture:
            return output
        print(output)
        return None

    # ── 未知命令：尝试模糊匹配 ──
    if cmd.startswith("/"):
        # 优先前缀匹配（如 "/sta" → "/status"）
        closest = _find_command_by_prefix(cmd)
        # 其次模糊匹配（如 "/sttatus" → "/status"）
        if not closest:
            closest = _find_closest_command(cmd)

        if closest and closest.lower() != cmd.lower():
            suggestion = f"{WARNING_PREFIX} 未找到命令 '{cmd}'，您是否想输入 '{closest}'？"
            if capture:
                return suggestion
            print(suggestion)
            return None

    # 不是已知命令，返回 None 让调用者交给 agent 处理
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

# REVIEW_PROMPT 和 REVIEW_ITERATION_PROMPT 现在从 miniagent.core.prompts.reviewer 导入
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
        _write(f"{WARNING_PREFIX} 审查服务不可用（LLM 无响应）", "ansired")
        return None

    has_issues = review_result.get("has_issues", False)
    issues = review_result.get("issues", [])
    improved = review_result.get("improved_answer")

    if not has_issues or not issues:
        _write(f"{SUCCESS_PREFIX} 未发现明显问题，答案质量良好。", "ansigreen")
        return None

    issue_summary = "；".join(i.get("description", "")[:60] for i in issues[:3])
    _write(f"{WARNING_PREFIX} 发现 {len(issues)} 个问题：{issue_summary}", "ansiyellow")
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
            _write(f"{WARNING_PREFIX} 审查服务不可用，返回当前最佳答案", "ansired")
            break

        has_issues = review_result.get("has_issues", False)
        new_issues = review_result.get("issues", [])
        new_improved = review_result.get("improved_answer")

        if not has_issues or not new_issues:
            _write(f"{SUCCESS_PREFIX} 第 {iteration + 1} 轮审查通过，无新问题。", "ansigreen")
            break

        prev_issue_count = len(new_issues)
        if new_improved:
            current_answer = new_improved
            issue_summary = "；".join(i.get("description", "")[:60] for i in new_issues[:2])
            _write(f"🔄 第 {iteration + 1} 轮发现 {len(new_issues)} 个问题，继续改进：{issue_summary}", "ansiyellow")
        else:
            _write(f"{WARNING_PREFIX} 第 {iteration + 1} 轮发现 {len(new_issues)} 个问题，但无法生成改进答案", "ansired")
            break
    else:
        _write(f"{WARNING_PREFIX} 已达到最大迭代次数（{max_iterations} 轮），返回最新答案", "ansiyellow")

    # 输出最终答案
    _write("\n--- 优化后的答案 ---", "ansigreen")
    if capture:
        return f"🔍 审查完成\n\n{current_answer[:2000]}"
    print(current_answer[:2000])
    return None


# ─── /improve 辅助函数 ───────────────────────────────

# IMPROVE_PROMPT 现在从 miniagent.core.prompts.improver 导入
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
    from miniagent.core.llm_json import llm_json

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
        from miniagent.infrastructure.cli_feishu_policy import focus_mode_status_line

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
    term_write: Any = None,
    capture: bool = False,
) -> str:
    """执行自测并返回结果。"""
    from miniagent.testing.test_runner import run_self_test

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

    _write("🧪 正在运行自测...", "ansicyan")

    report = await run_self_test(
        category=category,
        name_pattern=name_pattern,
        term_write=_write,
        mock=True,  # 默认使用 mock 模式，避免真实 LLM 调用
    )

    if capture:
        result_lines = [
            f"🧪 自测结果：{report.passed}/{report.total} 通过 ({report.pass_rate:.1%})",
            f"执行时间：{report.duration_seconds:.1f}s",
        ]
        if report.failed > 0:
            result_lines.append("\n失败的测试：")
            for r in report.results:
                if not r.passed:
                    result_lines.append(f"  ✗ {r.sample_name}: {r.error_message}")
        return "\n".join(result_lines)

    return ""


def _list_test_samples() -> str:
    """列出所有测试样本。"""
    from miniagent.testing.test_runner import TestRunner

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
    from miniagent.testing.test_runner import TestRunner

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
