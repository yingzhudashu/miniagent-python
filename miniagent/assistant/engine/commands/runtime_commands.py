"""依赖应用运行时资源的队列、重载和停止命令。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import TYPE_CHECKING, Any, cast

from miniagent.agent.types.error_prefix import ERROR_PREFIX, WARNING_PREFIX

if TYPE_CHECKING:
    from miniagent.assistant.engine.cli_state import CliLoopState


def _runtime(state: dict[str, Any]) -> Any | None:
    """返回显式注入的运行时容器。"""
    return state.get("runtime_ctx")


def _respond(output: str, *, capture: bool) -> str | None:
    """按渠道返回或打印命令结果。"""
    if capture:
        return output
    print(output)
    return None


def _capture_call(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """捕获仍采用 print 契约的旧叶子函数。"""
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            result = callable_(*args, **kwargs)
    except Exception as error:
        return f"{ERROR_PREFIX} 命令执行失败: {error}"
    return str(result) if isinstance(result, str) else output.getvalue().strip()


def _missing_runtime(capture: bool) -> str | None:
    return _respond(f"{WARNING_PREFIX} 运行时上下文未初始化", capture=capture)


async def handle_abort(
    _text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    message_queue_abort_chat_id: str | None = None,
    **_kwargs: Any,
) -> str | None:
    """中止当前渠道队列，但不终止 MiniAgent 进程。"""
    from miniagent.assistant.engine.commands.session_management import format_queue_abort_message

    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    queue = runtime.message_queue
    chat_id = (message_queue_abort_chat_id or "").strip() or queue.CLI_CHAT_ID
    return _respond(format_queue_abort_message(queue.abort_chat(chat_id)), capture=capture)


async def handle_background_task(
    text: str,
    *,
    state: dict[str, Any],
    engine: Any = None,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """启动、查询、读取、取消或清理后台任务。"""
    from miniagent.assistant.engine.btw_cmd import (
        cmd_btw_cancel,
        cmd_btw_clear,
        cmd_btw_result,
        cmd_btw_start,
        cmd_btw_status,
    )

    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    manager = runtime.background_tasks
    if subcommand == "start" and len(parts) >= 3:
        output = await cmd_btw_start(manager, engine, " ".join(parts[2:]), state)
    elif subcommand == "result" and len(parts) >= 3:
        output = await cmd_btw_result(manager, parts[2])
    elif subcommand == "cancel" and len(parts) >= 3:
        output = await cmd_btw_cancel(manager, parts[2])
    elif subcommand == "clear":
        output = cmd_btw_clear(manager)
    else:
        output = cmd_btw_status(manager, parts[2] if len(parts) >= 3 else None)
    return _respond(output, capture=capture)


async def handle_feishu(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """查询、启动或停止飞书生命周期服务。"""
    from miniagent.assistant.engine.feishu_lifecycle import FeishuRuntimeLifecycleService

    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    manager = runtime.lifecycle_manager
    if manager is None:
        return _respond(f"{ERROR_PREFIX} 飞书生命周期服务未初始化", capture=capture)
    service = manager.service("feishu")
    if not isinstance(service, FeishuRuntimeLifecycleService):
        return _respond(f"{ERROR_PREFIX} 飞书生命周期服务类型错误", capture=capture)
    try:
        if text.strip().lower() == "/feishu start":
            await service.activate()
            output = ""
        elif text.strip().lower() == "/feishu stop":
            await service.deactivate()
            output = ""
        else:
            output = _capture_call(runtime.feishu.status)
    except Exception as error:
        output = f"{ERROR_PREFIX} 命令执行失败: {error}"
    return _respond(output, capture=capture)


async def handle_query(
    _text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """显示当前渠道队列状态（``/queue status`` 的只读别名）。"""
    from miniagent.assistant.engine.commands.session_management import cmd_queue_status

    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    output = _capture_call(cmd_queue_status, runtime.message_queue, markdown=capture)
    return _respond(output, capture=capture)


async def handle_queue(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    message_queue_abort_chat_id: str | None = None,
    **_kwargs: Any,
) -> str | None:
    """查询、切换或中止消息队列。"""
    from miniagent.assistant.engine.commands.session_management import (
        cmd_queue_set,
        cmd_queue_status,
        format_queue_abort_message,
        format_queue_command_usage,
    )

    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    queue = runtime.message_queue
    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    if subcommand == "status":
        output = _capture_call(cmd_queue_status, queue, markdown=capture)
    elif subcommand in {"set", "mode"} and len(parts) >= 3:
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                await cmd_queue_set(queue, parts[2])
            output = buffer.getvalue().strip()
        except Exception as error:
            output = f"{ERROR_PREFIX} 命令执行失败: {error}"
    elif subcommand == "abort":
        chat_id = (message_queue_abort_chat_id or "").strip() or queue.CLI_CHAT_ID
        output = format_queue_abort_message(queue.abort_chat(chat_id))
    else:
        output = format_queue_command_usage(queue)
    return _respond(output, capture=capture)


async def handle_reload_skills(
    _text: str,
    *,
    state: dict[str, Any],
    registry: Any = None,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """刷新技能快照，并报告工具增删数量。"""
    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    try:
        from miniagent.assistant.skills.refresh import refresh_skills

        result = await refresh_skills(
            registry,
            runtime.skill_registry,
            state=state,
            session_manager=state.get("session_manager"),
        )
        output = (
            f"🔄 技能已重新加载\n"
            f"  包: {', '.join(result.package_ids) or '(无)'}\n"
            f"  技能数: {len(result.loaded_skills)}\n"
            f"  新增工具: {len(result.added_tools)}\n"
            f"  移除工具: {len(result.removed_tools)}"
        )
    except Exception as error:
        output = f"{ERROR_PREFIX} 技能 reload 失败: {error}"
    return _respond(output, capture=capture)


async def handle_stop(
    _text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """按统一关闭顺序停止当前运行时。"""
    from miniagent.assistant.engine.commands.session_management import (
        feishu_dot_commands_full_enabled,
    )
    from miniagent.assistant.engine.shutdown import shutdown_runtime

    runtime = _runtime(state)
    if runtime is None:
        return _missing_runtime(capture)
    if capture and not feishu_dot_commands_full_enabled():
        return f"{WARNING_PREFIX} /stop 命令只能在 CLI 使用（或设置 feishu.dot_commands_full=true）"
    await shutdown_runtime(
        runtime,
        cast("CliLoopState", state),
        reason="dot_stop_dispatch",
        release_cli_session_lock=True,
        call_unregister=True,
    )
    return "__EXIT__"


__all__ = [
    "handle_abort",
    "handle_background_task",
    "handle_feishu",
    "handle_query",
    "handle_queue",
    "handle_reload_skills",
    "handle_stop",
]
