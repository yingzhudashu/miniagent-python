"""Engine — 统一命令调度器

CLI 和飞书共享的命令路由，使用 `/` 前缀。

核心特性：
- print 捕获：CLI 命令原本用 print()，飞书需要返回字符串
- 不中断：`/status` 等检查命令不会打断正在运行的 agent
- 远程约束：飞书侧 `capture=True` 时默认 `allow_session_mutations_when_capture=False`，
  阻止 `/session switch/create/rename` 与 `/schedule` 变异；`MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1` 时放开

命令全集与用户说明见 ``docs/CLI.md``；飞书约束见 ``docs/FEISHU.md``。
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.logger import get_logger
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX

_logger = get_logger(__name__)


_REMOTE_SESSION_HINT = (
    "⚠️ 该命令会修改与 CLI 共享的会话状态，请在本地 MiniAgent 终端执行。\n"
    "飞书上可使用 /session list 查看会话列表。"
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
        allow_session_mutations_when_capture: capture=True 时是否允许执行 /session
            switch/create/rename（飞书应传 False，避免改 CLI 共享 state）
        feishu_user_status: capture=True 时若传入，则 /feishu start 用其写入全屏 transcript；
            为 None 且 capture=True 时用 print 捕获（飞书）；capture=False 时默认
            使用 ``_feishu_user_status_fn(runtime_ctx)``
        message_queue_abort_chat_id: 飞书入站 ``chat_id``（集成侧应传入）；供 ``/queue abort`` / ``/abort``
            定位要中止的 per-chat 队列。缺省为 CLI 专用 ``__cli__``。

    Returns:
        capture=True 时返回输出字符串，capture=False 时返回 None
    """
    # 仅支持 / 前缀（统一命令格式）
    if not text.startswith("/"):
        return None

    # 提取命令（去掉前缀）
    command = text[1:]

    from miniagent.engine.btw_cmd import (
        cmd_btw_cancel,
        cmd_btw_clear,
        cmd_btw_result,
        cmd_btw_start,
        cmd_btw_status,
    )
    from miniagent.engine.cli_commands import (
        cmd_bind,
        cmd_copy_transcript,
        cmd_help,
        cmd_instance_handler,
        cmd_kb_list,
        cmd_kb_mount,
        cmd_kb_reload,
        cmd_kb_search,
        cmd_kb_unmount,
        cmd_queue_set,
        cmd_queue_status,
        cmd_session_create,
        cmd_session_delete,
        cmd_session_list,
        cmd_session_rename,
        cmd_session_switch,
        cmd_unbind,
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

    # ── /stop：飞书 capture 默认拒绝；MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时与 CLI 相同
    if cmd == "/stop":
        if capture and not feishu_dot_commands_full_enabled():
            return f"{WARNING_PREFIX} /stop 命令只能在 CLI 使用（或设置 MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1）"
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
                    """启动飞书长轮询任务（动态获取当前技能配置）。"""
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
        elif text == "/feishu stop":
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
                output = f"❌ 命令执行失败: {e}"
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

    # ── copy: 复制助手回复 ──
    if cmd == "/copy":
        sm = state.get("session_manager")
        session_id = state.get("active_session_id", "")
        # 解析参数
        n = 1
        if len(parts) > 1:
            try:
                n = int(parts[1])
            except ValueError:
                pass  # 无效参数，使用默认值

        output = cmd_copy_transcript(sm, session_id, n)
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

    # ── /bind ──
    if cmd == "/bind":
        if channel_router is None:
            output = f"{WARNING_PREFIX} 通道路由器未初始化"
        else:
            args = parts[1:] if len(parts) > 1 else []
            output = cmd_bind(channel_router, args, state)
        if capture:
            return output
        print(output)
        return None

    # ── /unbind ──
    if cmd == "/unbind":
        if channel_router is None:
            output = f"{WARNING_PREFIX} 通道路由器未初始化"
        else:
            args = parts[1:] if len(parts) > 1 else []
            output = cmd_unbind(channel_router, args, state)
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

    # ── /kb（知识库）──
    if cmd == "/kb":
        sub_cmd = parts[1].lower() if len(parts) > 1 else ""
        md_kb = capture and feishu_markdown_commands_enabled()

        if sub_cmd == "list" or sub_cmd == "":
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
            registry = get_kb_registry()
            kb_list = registry.list()
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
            return output
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
        cc = getattr(engine, "confirmation_channel", None) if engine else None
        if cc is None or not cc.has_pending:
            output = f"{WARNING_PREFIX} 当前无待确认的请求"
        elif cmd == "/confirm":
            from miniagent.types.confirmation import ConfirmationResult

            cc.respond(ConfirmationResult(approved=True))
            output = f"{SUCCESS_PREFIX} 已确认，继续执行"
        elif cmd == "/reject":
            from miniagent.types.confirmation import ConfirmationResult

            cc.respond(ConfirmationResult(approved=False, rejected=True))
            output = f"{WARNING_PREFIX} 已拒绝，取消当前操作"
        else:
            # /adjust <新内容>
            adjustment = " ".join(parts[1:]).strip()
            if not adjustment:
                output = "用法：/adjust <调整后的内容>"
            else:
                from miniagent.types.confirmation import ConfirmationResult

                cc.respond(ConfirmationResult(approved=True, adjustment=adjustment))
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
        """输出文本：优先走 term_write（全屏 CLI），无 capture 时 fallback 到 print。"""
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

_IMPROVE_SYSTEM = """你是一个答案优化专家。根据质量评估建议改进答案。
返回 JSON 格式 {"improved_answer": "改进后的完整答案"}，不要包含其他文字。"""

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

返回 JSON 格式 {"improved_answer": "改进后的完整答案"}"""


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
        """输出文本：优先走 term_write（全屏 CLI），无 capture 时 fallback 到 print。"""
        if term_write and callable(term_write):
            try:
                term_write(text, color)
            except Exception:
                pass
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
        """输出文本：优先走 term_write（全屏 CLI），无 capture 时 fallback 到 print。"""
        """适配 cli_transcript_append 的签名 (style, text) -> None"""
        if term_write and callable(term_write):
            try:
                # cli_transcript_append 签名是 (style_cls, text)
                # 将 ansicyan 等转换为 class:ansicyan 格式
                style = f"class:{color}" if color else ""
                term_write(style, text)
            except Exception:
                pass
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
        return "📭 暂无测试记录，请先运行 `.test run`"

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
