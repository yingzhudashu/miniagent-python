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
    type: str = "primary",
) -> dict[str, Any]:
    """构建飞书卡片按钮 JSON。

    Args:
        label: 按钮显示文字
        miniagent_text: 点击后发送给 MiniAgent 的文本（常用于 .命令）
        chat_id: 关联的聊天 ID
        action_id: 操作标识符
        chat_type: 聊天类型（group/private）
        extra_value: 额外值字典
        type: 按钮样式（primary/danger/default）

    Returns:
        按钮 JSON 结构
    """
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
        "type": type,
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


def thinking_card_dict(
    cleaned_markdown: str,
    template: str,
    *,
    buttons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构建思考中卡片 JSON。

    Args:
        cleaned_markdown: 清理后的 Markdown 正文
        template: 卡片颜色模板
        buttons: 可选按钮列表
    """
    return build_interactive_card("💭 思考中", cleaned_markdown, template, buttons=buttons)


def confirmation_buttons() -> list[dict[str, Any]]:
    """返回确认/调整/拒绝按钮，供引擎在有待确认请求时附加到卡片。"""
    return [
        build_button(
            "✅ 确认",
            miniagent_text=".confirm",
            action_id="confirm",
        ),
        build_button(
            "✏️ 调整",
            miniagent_text=".adjust ",
            action_id="adjust",
        ),
        build_button(
            "❌ 拒绝",
            miniagent_text=".reject",
            action_id="reject",
            type="danger",
        ),
    ]


def reply_card_dict(title: str, body_markdown: str, template: str = "blue") -> dict[str, Any]:
    """构建回复卡片 JSON。

    Args:
        title: 卡片标题
        body_markdown: Markdown 正文
        template: 卡片颜色模板
    """
    return build_interactive_card(title, body_markdown, template)


__all__ = [
    "build_button",
    "build_interactive_card",
    "reply_card_dict",
    "thinking_card_dict",
    "confirmation_buttons",
]
