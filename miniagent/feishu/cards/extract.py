"""从飞书 interactive 卡片 JSON 抽取可读文本。"""

from __future__ import annotations

import json
from typing import Any

from miniagent.feishu.cards.sanitize import sanitize_card_text

_MAX_NODES = 400
_MAX_DEPTH = 12


def _text_from_obj(obj: Any) -> str:
    """从飞书卡片组件对象中提取文本内容（支持 plain_text、lark_md、div、button 等）。"""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        tag = str(obj.get("tag") or "").lower()
        if tag in ("plain_text", "lark_md"):
            return str(obj.get("content") or obj.get("text") or "")
        if tag == "text":
            inner = obj.get("text")
            if isinstance(inner, dict):
                return str(inner.get("content") or "")
            return str(inner or "")
        if tag == "div":
            t = obj.get("text")
            if isinstance(t, dict):
                return _text_from_obj(t)
        if tag == "button":
            t = obj.get("text")
            if isinstance(t, dict):
                return str(t.get("content") or "")
        return ""
    return ""


def _walk(node: Any, *, depth: int, budget: list[int], parts: list[str]) -> None:
    """递归遍历卡片 JSON 结构并收集文本片段。"""
    if budget[0] <= 0 or depth > _MAX_DEPTH:
        return
    budget[0] -= 1
    if isinstance(node, dict):
        t = _text_from_obj(node)
        if t.strip():
            parts.append(t.strip())
        for key in ("elements", "body", "content", "fields", "actions", "columns"):
            child = node.get(key)
            if isinstance(child, list):
                for item in child:
                    _walk(item, depth=depth + 1, budget=budget, parts=parts)
            elif isinstance(child, dict):
                _walk(child, depth=depth + 1, budget=budget, parts=parts)
        if str(node.get("tag") or "").lower() == "column_set":
            for col in node.get("columns") or []:
                _walk(col, depth=depth + 1, budget=budget, parts=parts)
    elif isinstance(node, list):
        for item in node:
            _walk(item, depth=depth + 1, budget=budget, parts=parts)


def extract_text_from_interactive_content(content_str: str) -> str:
    """从消息 ``content`` JSON 字符串抽取卡片正文。"""
    raw = (content_str or "").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return sanitize_card_text(raw)

    parts: list[str] = []
    budget = [_MAX_NODES]
    if isinstance(data, dict):
        _walk(data, depth=0, budget=budget, parts=parts)
        body = data.get("body")
        if isinstance(body, dict):
            _walk(body, depth=0, budget=budget, parts=parts)
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, list):
                for cell in row:
                    _walk(cell, depth=0, budget=budget, parts=parts)
            else:
                _walk(row, depth=0, budget=budget, parts=parts)

    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return sanitize_card_text("\n".join(uniq))


def inbound_text_from_message(msg_type: str, content_str: str) -> str | None:
    """若应把入站当作文本处理，返回合成用户消息；否则 ``None``。"""
    mt = (msg_type or "").strip().lower()
    if mt == "text":
        try:
            parsed = json.loads(content_str or "{}")
            return str(parsed.get("text") or "")
        except (json.JSONDecodeError, TypeError):
            return content_str
    if mt == "interactive":
        extracted = extract_text_from_interactive_content(content_str)
        if extracted.strip():
            return f"[飞书卡片]\n{extracted}"
    return None


__all__ = ["extract_text_from_interactive_content", "inbound_text_from_message"]
