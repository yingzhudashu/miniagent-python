"""会话命令的独立解析、锁协调与远程权限策略。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, WARNING_PREFIX

_REMOTE_SESSION_HINT = (
    "⚠️ 该命令会修改与 CLI 共享的会话状态，请在本地 MiniAgent 终端执行。\n"
    "飞书上可使用 /session list 查看会话列表。"
)


def _capture(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """捕获同步会话叶子命令的输出。"""
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = callable_(*args, **kwargs)
    except Exception as error:
        return f"{ERROR_PREFIX} 命令执行失败: {error}"
    return str(result) if isinstance(result, str) else buffer.getvalue().strip()


async def handle_session(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    allow_session_mutations_when_capture: bool = True,
    **_kwargs: Any,
) -> str | None:
    """列出或安全修改会话，并同步活动会话 ID。"""
    from miniagent.assistant.engine.commands.session_management import (
        feishu_dot_commands_full_enabled,
        feishu_markdown_commands_enabled,
        format_session_command_usage,
    )

    runtime = state.get("runtime_ctx")
    manager = state.get("session_manager")
    output: str | None
    if runtime is None or manager is None:
        output = f"{WARNING_PREFIX} 运行时上下文或会话管理器未初始化"
    else:
        parts = text.split()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        remote_allowed = (
            allow_session_mutations_when_capture or feishu_dot_commands_full_enabled()
        )
        if capture and not remote_allowed and subcommand != "list":
            output = _REMOTE_SESSION_HINT
        else:
            output = await _dispatch_session_subcommand(
                parts,
                subcommand,
                state,
                runtime,
                markdown=capture and feishu_markdown_commands_enabled(),
            )
            if output is None:
                output = format_session_command_usage()
    if capture:
        return output
    print(output)
    return None


async def _dispatch_session_subcommand(
    parts: list[str],
    subcommand: str,
    state: dict[str, Any],
    runtime: Any,
    *,
    markdown: bool,
) -> str | None:
    """执行一个已授权的会话子命令。"""
    from miniagent.assistant.engine.commands.session_management import (
        cmd_session_delete,
        cmd_session_list,
        cmd_session_rename,
        cmd_session_switch,
    )
    from miniagent.assistant.engine.session_lock import (
        is_session_locked,
        release_session_lock,
        try_lock_session_async,
    )

    manager = state["session_manager"]
    active = str(state.get("active_session_id", ""))
    if subcommand == "list":
        return _capture(cmd_session_list, manager, active, markdown=markdown)
    if subcommand == "switch" and len(parts) >= 3:
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                active = await cmd_session_switch(
                    manager,
                    active,
                    parts[2],
                    try_lock_session_async,
                    release_session_lock,
                    is_session_locked,
                    runtime.channel_router,
                    state.get("feishu_p2p_synced_senders")
                    if isinstance(state.get("feishu_p2p_synced_senders"), set)
                    else None,
                )
            state["active_session_id"] = active
            return buffer.getvalue().strip()
        except Exception as error:
            return f"{ERROR_PREFIX} 命令执行失败: {error}"
    if subcommand == "create" and len(parts) >= 3:
        return await _create_session(parts, manager, try_lock_session_async)
    if subcommand == "rename" and len(parts) >= 4:
        return _capture(cmd_session_rename, manager, parts[2], " ".join(parts[3:]))
    if subcommand == "delete" and len(parts) >= 3:
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                await cmd_session_delete(manager, active, parts[2], release_session_lock)
            return buffer.getvalue().strip()
        except Exception as error:
            return f"{ERROR_PREFIX} 命令执行失败: {error}"
    return None


async def _create_session(parts: list[str], manager: Any, lock: Any) -> str:
    """创建会话并捕获异步叶子命令输出。"""
    from miniagent.assistant.engine.commands.session_management import cmd_session_create

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            await cmd_session_create(
                manager,
                parts[2],
                parts[3] if len(parts) > 3 else None,
                lock,
            )
        return buffer.getvalue().strip()
    except Exception as error:
        return f"{ERROR_PREFIX} 命令执行失败: {error}"


__all__ = ["handle_session"]
