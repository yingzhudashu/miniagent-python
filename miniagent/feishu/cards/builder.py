"""飞书 interactive 卡片 JSON 构建。"""

from __future__ import annotations

from typing import Any


def build_button(
    label: str,
    *,
    miniagent_text: str | None = None,
    chat_id: str | None = None,
    action_id: str | None = None,
    chat_type: str = "group",
    extra_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = dict(extra_value or {})
    if miniagent_text:
        value["miniagent_text"] = miniagent_text
        value.setdefault("text", miniagent_text)
    if chat_id:
        value["chat_id"] = chat_id
    if action_id:
        value["action_id"] = action_id
    if chat_type:
        value["chat_type"] = chat_type
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": (label or "确定")[:80]},
        "type": "primary",
        "value": value,
    }


def build_interactive_card(
    header_title: str,
    body_markdown: str,
    template: str = "blue",
    *,
    buttons: list[dict[str, Any]] | None = None,
    extra_elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构建 v1 交互卡片（``elements`` + ``lark_md``）。"""
    elements: list[dict[str, Any]] = list(extra_elements or [])
    body = (body_markdown or "").strip()
    if body:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body}})
    if buttons:
        elements.append({"tag": "action", "actions": buttons})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": (header_title or "Mini Agent")[:200]},
            "template": template or "blue",
        },
        "elements": elements,
    }


def thinking_card_dict(cleaned_markdown: str, template: str) -> dict[str, Any]:
    return build_interactive_card("💭 思考中", cleaned_markdown, template)


def reply_card_dict(title: str, body_markdown: str, template: str = "blue") -> dict[str, Any]:
    return build_interactive_card(title, body_markdown, template)


__all__ = [
    "build_button",
    "build_interactive_card",
    "reply_card_dict",
    "thinking_card_dict",
]
