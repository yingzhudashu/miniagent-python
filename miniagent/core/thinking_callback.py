"""思考回调适配层 — ``on_thinking(text, streaming, header[, full_record])``

引擎与执行器在流式输出规划/推理片段时调用 ``on_thinking``；部分上层（如飞书）只需摘要，
而会话落盘需要完整思考文本。本模块用 ``inspect.signature`` 判断是否传入 ``full_record``，
避免破坏仅接受三参的旧回调。

参见 ``docs/ARCHITECTURE.md``（思考展示与历史）；``invoke_on_thinking`` 由 ``agent`` / ``executor`` 间接调用。
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

_logger = logging.getLogger(__name__)

__all__ = ["invoke_on_thinking"]


async def invoke_on_thinking(
    cb: Callable[..., Awaitable[Any]] | None,
    text: str,
    streaming: bool,
    header: str,
    *,
    full_record: str | None = None,
) -> None:
    """调用 ``on_thinking``；若签名含 ``full_record`` 或 ``**kwargs``，则尝试传入 ``full_record``。"""
    if cb is None:
        return
    try:
        sig = inspect.signature(cb)
        params = sig.parameters
        has_fr = "full_record" in params
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if full_record is not None and (has_fr or has_varkw):
            try:
                await cb(text, streaming, header, full_record=full_record)
                return
            except TypeError:
                pass
        await cb(text, streaming, header)
    except TypeError:
        try:
            await cb(text, streaming, header)
        except Exception:
            _logger.debug("invoke_on_thinking 三参回退仍失败", exc_info=True)
    except Exception:
        _logger.debug("invoke_on_thinking 回调异常", exc_info=True)
