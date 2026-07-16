"""按客户端、端点、协议和模型学习不支持的 LLM 参数。"""

from __future__ import annotations

import re
import threading
import weakref
from collections import OrderedDict
from typing import Any

from miniagent.llm.types import OpenAIWireAPI

_LOCK = threading.Lock()
_CLIENT_BUCKETS: weakref.WeakKeyDictionary[
    Any, OrderedDict[tuple[str, str, str], set[str]]
] = weakref.WeakKeyDictionary()
_FALLBACK: OrderedDict[tuple[int, str, str, str], set[str]] = OrderedDict()
_FALLBACK_MAX = 128
_CLIENT_MAX = 64


def _endpoint(client: Any) -> str:
    """读取有界端点字符串，避免缓存键携带任意大对象文本。"""
    return str(getattr(client, "base_url", "") or "")[:512]


def unsupported_parameter_names(error: Exception) -> set[str]:
    """仅从 400 错误中提取明确声明不支持的采样参数。"""
    if getattr(error, "status_code", None) not in (400, "400"):
        return set()
    message = str(error).lower()
    names: set[str] = set()
    for name in ("temperature", "top_p"):
        patterns = (
            rf"unsupported\s+(?:request\s+)?parameters?\s*[:=]?\s*['\"]?{name}",
            rf"unknown\s+(?:request\s+)?parameters?\s*[:=]?\s*['\"]?{name}",
            rf"unrecognized\s+(?:request\s+)?(?:argument|parameter).*['\"]?{name}",
            rf"{name}['\"]?\s+(?:parameter\s+)?(?:(?:is|are)\s+)?not\s+supported",
            rf"does\s+not\s+support[^.\n]{{0,40}}{name}",
            rf"不支持[^。\n]{{0,20}}{name}|{name}[^。\n]{{0,20}}不支持",
        )
        if any(re.search(pattern, message) for pattern in patterns):
            names.add(name)
    return names


def _bucket(client: Any, *, create: bool) -> OrderedDict[tuple[str, str, str], set[str]] | None:
    """返回弱引用客户端桶；不可弱引用对象改用有界 fallback。"""
    try:
        bucket = _CLIENT_BUCKETS.get(client)
        if bucket is None and create:
            bucket = OrderedDict()
            _CLIENT_BUCKETS[client] = bucket
        return bucket
    except TypeError:
        return None


def learn_unsupported_params(
    client: Any,
    params: dict[str, Any],
    wire_api: OpenAIWireAPI,
    error: Exception,
) -> None:
    """记录一次明确的参数不支持响应，并执行有界 LRU 驱逐。"""
    names = unsupported_parameter_names(error) & params.keys()
    if not names:
        return
    model = str(params.get("model") or "")
    endpoint = _endpoint(client)
    with _LOCK:
        bucket = _bucket(client, create=True)
        if bucket is not None:
            client_key = (endpoint, wire_api, model)
            bucket.setdefault(client_key, set()).update(names)
            bucket.move_to_end(client_key)
            while len(bucket) > _CLIENT_MAX:
                bucket.popitem(last=False)
            return
        fallback_key = (id(client), endpoint, wire_api, model)
        _FALLBACK.setdefault(fallback_key, set()).update(names)
        _FALLBACK.move_to_end(fallback_key)
        while len(_FALLBACK) > _FALLBACK_MAX:
            _FALLBACK.popitem(last=False)


def apply_learned_capabilities(
    client: Any,
    params: dict[str, Any],
    wire_api: OpenAIWireAPI,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """移除已知不支持参数，并发出不含正文的调整 Trace。"""
    model = str(params.get("model") or "")
    endpoint = _endpoint(client)
    with _LOCK:
        bucket = _bucket(client, create=False)
        if bucket is not None:
            client_key = (endpoint, wire_api, model)
            unsupported = set(bucket.get(client_key, set()))
            if client_key in bucket:
                bucket.move_to_end(client_key)
        else:
            fallback_key = (id(client), endpoint, wire_api, model)
            unsupported = set(_FALLBACK.get(fallback_key, set()))
            if fallback_key in _FALLBACK:
                _FALLBACK.move_to_end(fallback_key)
    removed = tuple(sorted(name for name in unsupported if name in params))
    if not removed:
        return params, ()
    adjusted = {name: value for name, value in params.items() if name not in removed}
    return adjusted, removed


__all__ = [
    "apply_learned_capabilities",
    "learn_unsupported_params",
    "unsupported_parameter_names",
]
