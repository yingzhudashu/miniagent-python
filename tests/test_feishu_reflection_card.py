"""``send_reflection_card`` 出站卡片测试。"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from miniagent.assistant.feishu.poll_server import send_reflection_card
from miniagent.assistant.feishu.types import FeishuConfig


@dataclass
class _FakeReflection:
    acceptable: bool
    quality_score: float = 0.85
    suggestions: list[str] | None = None


@pytest.mark.asyncio
async def test_send_reflection_card_posts_interactive_message() -> None:
    cfg = FeishuConfig(app_id="a", app_secret="b")
    reflection = _FakeReflection(acceptable=True, suggestions=["更简洁"])

    with patch(
        "miniagent.assistant.feishu.thinking_delivery._post_interactive_message_async",
        new_callable=AsyncMock,
        return_value=(True, "om_card"),
    ) as post:
        await send_reflection_card(
            cfg,
            "oc_chat1234567890",
            reflection,
            reply_to_message_id="om_trigger",
            thread_id="thr_1",
        )

    post.assert_called_once()
    kwargs = post.call_args.kwargs
    assert kwargs["receive_id"] == "oc_chat1234567890"
    assert kwargs["reply_to_message_id"] == "om_trigger"
    assert kwargs["reply_in_thread"] is True
    card = kwargs["card_json"]
    assert "质量评估" in card
    assert "0.8" in card


@pytest.mark.asyncio
async def test_send_reflection_card_includes_issues() -> None:
    cfg = FeishuConfig(app_id="a", app_secret="b")
    reflection = _FakeReflection(
        acceptable=False,
        quality_score=0.4,
        suggestions=["更简洁"],
    )
    reflection.issues = ["信息不完整"]  # type: ignore[attr-defined]

    with patch(
        "miniagent.assistant.feishu.thinking_delivery._post_interactive_message_async",
        new_callable=AsyncMock,
        return_value=(True, "om_card"),
    ) as post:
        await send_reflection_card(cfg, "oc_chat1234567890", reflection)

    card = post.call_args.kwargs["card_json"]
    assert "发现问题" in card
    assert "信息不完整" in card


@pytest.mark.asyncio
async def test_send_reflection_card_skips_invalid_chat_id() -> None:
    cfg = FeishuConfig(app_id="a", app_secret="b")
    with patch(
        "miniagent.assistant.feishu.thinking_delivery._post_interactive_message_async",
        new_callable=AsyncMock,
        return_value=(True, "om_card"),
    ) as post:
        await send_reflection_card(cfg, "", _FakeReflection(acceptable=False))
    post.assert_not_called()
