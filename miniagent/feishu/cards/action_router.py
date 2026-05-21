"""卡片按钮回调 value → 入站 Agent 文本。"""

from __future__ import annotations

import json
from typing import Any


def inbound_text_from_card_action_value(value: dict[str, Any] | None) -> str | None:
    """从 ``action.value`` 解析可调度文本；无 ``miniagent_text`` 时可用 ``action_id`` + form。"""
    value = dict(value or {})
    text = str(value.get("miniagent_text") or value.get("text") or "").strip()
    action_id = str(value.get("action_id") or "").strip()
    form_payload = value.get("form") or value.get("form_value")
    if not text and action_id:
        fp = ""
        if form_payload is not None:
            try:
                fp = json.dumps(form_payload, ensure_ascii=False)[:2000]
            except (TypeError, ValueError):
                fp = str(form_payload)[:2000]
        text = f"[卡片操作] action_id={action_id}"
        if fp:
            text += f" payload={fp}"
    return text or None


__all__ = ["inbound_text_from_card_action_value"]
