"""计划确认、调整和拒绝命令处理器。"""

from __future__ import annotations

from typing import Any

from miniagent.agent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX


async def handle_confirmation(
    text: str,
    *,
    state: dict[str, Any],
    engine: Any = None,
    capture: bool = False,
    confirmation_session_key: str | None = None,
    **_kwargs: Any,
) -> str | None:
    """把确认命令路由到当前会话专属的确认通道。"""
    from miniagent.agent.types.confirmation import (
        ConfirmationResult,
        ConfirmationStage,
    )
    from miniagent.assistant.engine.parallel_config import resolve_active_session_key

    parts = text.split()
    command = parts[0].lower()
    runtime = state.get("runtime_ctx")
    if runtime is None or engine is None:
        output = f"{WARNING_PREFIX} 运行时上下文或执行引擎未初始化"
    else:
        session_key = confirmation_session_key or resolve_active_session_key(
            runtime.channel_router,
            state.get("active_session_id") or "default",
        )
        engine.set_active_session_key(session_key)
        channel = engine.get_confirmation_channel(session_key)
        if channel is None or not channel.has_pending:
            output = f"{WARNING_PREFIX} 当前无待确认的请求"
        elif command == "/confirm":
            channel.respond(ConfirmationResult.confirm())
            output = f"{SUCCESS_PREFIX} 已确认，继续执行"
        elif command == "/reject":
            channel.respond(ConfirmationResult.reject())
            output = f"{WARNING_PREFIX} 已拒绝，取消当前操作"
        else:
            adjustment = " ".join(parts[1:]).strip()
            if adjustment:
                channel.respond(ConfirmationResult.adjust(adjustment))
                suffix = "…" if len(adjustment) > 60 else ""
                output = f"{SUCCESS_PREFIX} 已调整并确认：{adjustment[:60]}{suffix}"
            else:
                output = "用法：/adjust <调整后的内容>"
                pending = channel.pending
                if pending and pending.stage == ConfirmationStage.PLAN:
                    reference = (pending.full_content or pending.content or "").strip()
                    if reference:
                        preview = reference if len(reference) <= 2000 else f"{reference[:2000]}…"
                        output = f"{output}\n\n当前完整计划：\n{preview}"
    if capture:
        return output
    print(output)
    return None


__all__ = ["handle_confirmation"]
