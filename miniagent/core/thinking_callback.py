"""Invoke the canonical thinking callback contract.

All thinking consumers receive the stable three positional fields plus the complete
keyword-only metadata set. Callback failures are isolated from the Agent execution
path because thinking output is observational UI state.
"""

from __future__ import annotations

from miniagent.infrastructure.logger import get_logger
from miniagent.types.protocols import OnThinkingCallback

_logger = get_logger(__name__)

__all__ = ["invoke_on_thinking"]


async def invoke_on_thinking(
    cb: OnThinkingCallback | None,
    text: str,
    streaming: bool,
    header: str,
    *,
    full_record: str | None = None,
    reset: bool = False,
    is_last_step: bool = False,
) -> None:
    """Call a thinking observer with the canonical callback signature."""
    if cb is None:
        return
    try:
        await cb(
            text,
            streaming,
            header,
            full_record=full_record,
            reset=reset,
            is_last_step=is_last_step,
        )
    except Exception:
        _logger.debug("invoke_on_thinking 回调异常", exc_info=True)
