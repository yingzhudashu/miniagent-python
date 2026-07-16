"""CLI 与飞书共享的无状态或轻状态基础命令处理器。

这些处理器直接绑定到 :class:`CommandSpec`，避免所有命令先进入巨型兼容分派器。
每个处理器只解析自己的参数，并沿用 ``capture`` 契约：飞书/全屏 TUI 返回文本，
普通 fallback CLI 则打印文本。
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def _respond(output: str, *, capture: bool) -> str | None:
    """按调用渠道返回或打印同一份用户可见文本。"""
    if capture:
        return output
    print(output)
    return None


async def handle_status(
    _text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """显示当前 Agent、会话和队列状态。"""
    from miniagent.assistant.engine.command_dispatch import _format_status

    return _respond(_format_status(state), capture=capture)


async def handle_model(
    text: str,
    *,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """显示当前模型，或切换到显式指定的模型。"""
    from miniagent.assistant.engine.model_cmd import format_model_info, switch_model

    parts = text.split()
    output = switch_model(parts[1]) if len(parts) > 1 else format_model_info()
    return _respond(output, capture=capture)


async def handle_doctor(
    _text: str,
    *,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """运行只读环境诊断并展示可操作建议。"""
    from miniagent.assistant.engine.doctor import diagnose_environment

    return _respond(diagnose_environment(), capture=capture)


async def handle_config(
    text: str,
    *,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """显示全部有效配置或一个配置节。"""
    from miniagent.assistant.engine.config_cmd import format_config_info

    parts = text.split()
    section = parts[1] if len(parts) > 1 else None
    return _respond(format_config_info(section), capture=capture)


async def handle_stats(
    _text: str,
    *,
    monitor: Any = None,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """显示运行监控统计；监控未注入时给出明确降级说明。"""
    output = str(monitor.report()) if monitor is not None else f"{WARNING_PREFIX} 监控器未初始化"
    return _respond(output, capture=capture)


async def handle_help(
    _text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """从命令元数据生成并显示帮助。"""
    from miniagent.assistant.engine.commands.session_management import cmd_help

    runtime = state.get("runtime_ctx")
    if runtime is None:
        return _respond(f"{WARNING_PREFIX} 运行时上下文未初始化", capture=capture)
    if not capture:
        cmd_help(runtime.message_queue, state.get("instance_id"))
        return None
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cmd_help(runtime.message_queue, state.get("instance_id"))
    return buffer.getvalue().strip()


async def handle_schedule(
    text: str,
    *,
    capture: bool = False,
    allow_session_mutations_when_capture: bool = True,
    **_kwargs: Any,
) -> str | None:
    """查询或修改定时任务；受限远程渠道只能执行只读子命令。"""
    from miniagent.assistant.engine.commands.session_management import (
        cmd_schedule,
        feishu_dot_commands_full_enabled,
    )

    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    mutating = subcommand in {"add", "remove", "enable", "disable"}
    remote_allowed = allow_session_mutations_when_capture or feishu_dot_commands_full_enabled()
    output = cmd_schedule(text, allow_mutations=not (capture and mutating and not remote_allowed))
    return _respond(output, capture=capture)


async def handle_reload_config(
    _text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """原子重载运行时配置，并把校验错误映射为用户错误。"""
    from miniagent.assistant.infrastructure.json_config import reload_runtime_config

    runtime = state.get("runtime_ctx")
    if runtime is None:
        return _respond(f"{WARNING_PREFIX} 运行时上下文未初始化", capture=capture)
    try:
        await reload_runtime_config(runtime)
        output = f"{SUCCESS_PREFIX} 配置已重新加载"
    except Exception as error:
        output = f"{ERROR_PREFIX} 配置加载失败: {error}"
    return _respond(output, capture=capture)


__all__ = [
    "handle_config",
    "handle_doctor",
    "handle_help",
    "handle_model",
    "handle_reload_config",
    "handle_schedule",
    "handle_stats",
    "handle_status",
]
