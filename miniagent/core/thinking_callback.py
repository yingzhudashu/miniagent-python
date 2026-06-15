"""思考回调适配层 — ``on_thinking(text, streaming, header[, 扩展关键字参数])``

引擎与执行器在流式输出规划/推理片段时调用 ``on_thinking``；部分上层（如飞书）只需摘要，
而会话落盘需要完整思考文本。本模块用 ``inspect.signature`` 判断是否传入扩展关键字参数
（``full_record``、``reset``、``is_last_step``），避免破坏仅接受三参的旧回调。

扩展参数语义：
- ``full_record``：完整思考文本，供 ``UnifiedEngine`` 写入会话 ``thinking`` 历史。
- ``reset=True``：清除该 ``header`` 下已聚合的流式状态（如澄清后重新展示规划）。
- ``is_last_step=True``：标记规划最后一步，UI 可不在思考区重复展示 LLM 正文。

三参旧回调 ``(text, streaming, header)`` 始终兼容；扩展参调用若因签名不匹配触发
``TypeError``，会自动降级为三参调用。

参见 ``docs/ARCHITECTURE.md``（思考展示与历史）；``invoke_on_thinking`` 由 ``agent`` / ``executor`` 间接调用。

**性能优化**：
- 签名检查缓存（``WeakKeyDictionary``，避免每次调用 ``inspect.signature``）
"""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Awaitable, Callable
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

__all__ = ["invoke_on_thinking"]

# ── 性能优化：签名检查缓存 ──
# WeakKeyDictionary 按对象身份缓存；对象被 GC 后条目自动失效，避免 id 复用导致误命中
_sig_cache: weakref.WeakKeyDictionary[
    Callable[..., Awaitable[Any]], dict[str, bool]
] = weakref.WeakKeyDictionary()


def _get_signature_info(cb: Callable[..., Awaitable[Any]]) -> dict[str, bool]:
    """获取函数签名信息（带缓存）。

    缓存键为回调对象本身（``WeakKeyDictionary``）；对象被 GC 后条目自动失效，
    避免 ``id`` 复用导致误命中。不可弱引用的回调（极少见）每次重新解析签名。
    签名解析失败时返回全 ``False``，调用方将降级为三参模式。

    Args:
        cb: 回调函数

    Returns:
        包含 ``has_fr``、``has_reset``、``has_last``、``has_varkw`` 的字典
    """
    cached = _sig_cache.get(cb)
    if cached is not None:
        return cached

    try:
        sig = inspect.signature(cb)
        params = sig.parameters
        info = {
            "has_fr": "full_record" in params,
            "has_reset": "reset" in params,
            "has_last": "is_last_step" in params,
            "has_varkw": any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()),
        }
        try:
            _sig_cache[cb] = info
        except TypeError:
            # 极少数不可弱引用的 callable，跳过缓存
            pass
        return info
    except (ValueError, TypeError):
        # 签名获取失败，返回默认值
        return {
            "has_fr": False,
            "has_reset": False,
            "has_last": False,
            "has_varkw": False,
        }


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
    """调用 ``on_thinking``；若签名含扩展参数或 ``**kwargs``，则按需传入。

    Args:
        cb: 回调函数
        text: 思考内容文本
        streaming: 是否流式输出
        header: 阶段标签（如 ``[评估与计划]``）
        full_record: 完整记录文本（用于会话历史落盘）
        reset: 是否重置该 header 的聚合状态（用于清除重复内容）
        is_last_step: 是否为规划的最后一步（最后一步的 LLM 正文不在思考区显示，避免重复）

    Returns:
        None

    Note:
        思考展示属于辅助 UI 路径：回调抛出的任何异常均被捕获并记 ``debug`` 日志，
        不会向上传播，以免中断 agent 主流程。扩展参调用若触发 ``TypeError``（签名不匹配），
        会自动回退为三参 ``cb(text, streaming, header)``。
    """
    if cb is None:
        return

    # 性能优化：使用缓存的签名信息
    sig_info = _get_signature_info(cb)
    has_fr = sig_info["has_fr"]
    has_reset = sig_info["has_reset"]
    has_last = sig_info["has_last"]
    has_varkw = sig_info["has_varkw"]

    # 构建可选参数字典
    extra_kwargs: dict[str, Any] = {}
    if full_record is not None and (has_fr or has_varkw):
        extra_kwargs["full_record"] = full_record
    if reset and (has_reset or has_varkw):
        extra_kwargs["reset"] = reset
    if is_last_step and (has_last or has_varkw):
        extra_kwargs["is_last_step"] = is_last_step

    try:
        if extra_kwargs:
            try:
                await cb(text, streaming, header, **extra_kwargs)
                return
            except TypeError as e:
                _logger.debug("回调参数不匹配，尝试回退: %s", e)
        await cb(text, streaming, header)
    except Exception:
        _logger.debug("invoke_on_thinking 回调异常", exc_info=True)
