"""Chat Completions 请求体清洗 — 剥离 ``_*`` 自定义键

内部管线可能在 ``messages`` 中附带以下划线开头的元数据（例如调试字段）；OpenAI 兼容 API
会拒绝未知顶层字段。本模块在每次 ``chat.completions.create`` 前浅拷贝并剔除这些键，
同时清理 ``tool_calls[]`` 内嵌 dict 的同名字段。

若新增消息字段，要么使用 API 认可的名字，要么保留 ``_`` 前缀并依赖此处剥离。"""

from __future__ import annotations

from typing import Any


def strip_leading_underscore_keys_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """浅拷贝每条消息，移除顶层及 ``tool_calls[]`` 项内的 ``_*`` 键。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        d: dict[str, Any] = {
            k: v
            for k, v in m.items()
            if isinstance(k, str) and not k.startswith("_")
        }
        tc = d.get("tool_calls")
        if isinstance(tc, list):
            cleaned: list[Any] = []
            for item in tc:
                if isinstance(item, dict):
                    cleaned.append(
                        {
                            kk: vv
                            for kk, vv in item.items()
                            if isinstance(kk, str) and not kk.startswith("_")
                        }
                    )
                else:
                    cleaned.append(item)
            d["tool_calls"] = cleaned
        out.append(d)
    return out


__all__ = ["strip_leading_underscore_keys_from_messages"]
