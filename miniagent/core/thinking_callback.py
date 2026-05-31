"""思考回调适配层 — ``on_thinking(text, streaming, header[, full_record])``

引擎与执行器在流式输出规划/推理片段时调用 ``on_thinking``；部分上层（如飞书）只需摘要，
而会话落盘需要完整思考文本。本模块用 ``inspect.signature`` 判断是否传入 ``full_record``，
避免破坏仅接受三参的旧回调。

参见 ``docs/ARCHITECTURE.md``（思考展示与历史）；``invoke_on_thinking`` 由 ``agent`` / ``executor`` 间接调用。
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

__all__ = ["invoke_on_thinking"]


async def invoke_on_thinking(
    cb: Callable[..., Awaitable[Any]] | None,
    text: str,
    streaming: bool,
    header: str,
    *,
    full_record: str | None = None,
    reset: bool = False,
    is_last_step: bool = False,
) -> None:
    """调用 ``on_thinking``；若签名含 ``full_record`` 或 ``reset`` 或 ``is_last_step`` 或 ``**kwargs``，则尝试传入。

    Args:
        cb: 回调函数
        text: 思考内容文本
        streaming: 是否流式输出
        header: 阶段标签（如 ``[评估与计划]``）
        full_record: 完整记录文本（用于会话历史落盘）
        reset: 是否重置该 header 的聚合状态（用于清除重复内容）
        is_last_step: 是否为规划的最后一步（最后一步的 LLM 正文不在思考区显示，避免重复）
    """
    if cb is None:
        return
    try:
        sig = inspect.signature(cb)
        params = sig.parameters
        has_fr = "full_record" in params
        has_reset = "reset" in params
        has_last = "is_last_step" in params
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        # 构建可选参数字典
        extra_kwargs: dict[str, Any] = {}
        if full_record is not None and (has_fr or has_varkw):
            extra_kwargs["full_record"] = full_record
        if reset and (has_reset or has_varkw):
            extra_kwargs["reset"] = reset
        if is_last_step and (has_last or has_varkw):
            extra_kwargs["is_last_step"] = is_last_step
        if extra_kwargs:
            try:
                await cb(text, streaming, header, **extra_kwargs)
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
