"""飞书入站消息防抖单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.feishu.message_debounce import (
    FeishuMessageDebouncer,
    feishu_message_debounce_ms,
    reset_feishu_message_debouncer,
)
from miniagent.feishu.types import FeishuInboundText


def _inbound(text: str, *, mid: str = "om_1") -> FeishuInboundText:
    return FeishuInboundText(
        text=text,
        chat_id="oc_chat",
        sender_id="ou_user",
        chat_type="group",
        message_id=mid,
    )


@pytest.mark.asyncio
async def test_debouncer_merges_messages_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.feishu.message_debounce.get_config",
        lambda key, default=None: 50 if key == "feishu.message_debounce_ms" else default,
    )
    debouncer = FeishuMessageDebouncer()
    flushed: list[tuple[str, list[str]]] = []

    async def on_flush(merged: FeishuInboundText, ids: list[str]) -> None:
        flushed.append((merged.text, ids))

    await debouncer.schedule(_inbound("hello", mid="om_a"), debounce_ms=50, on_flush=on_flush)
    await debouncer.schedule(_inbound("world", mid="om_b"), debounce_ms=50, on_flush=on_flush)
    await asyncio.sleep(0.12)

    assert len(flushed) == 1
    assert flushed[0][0] == "hello\nworld"
    assert flushed[0][1] == ["om_a", "om_b"]


@pytest.mark.asyncio
async def test_debouncer_zero_ms_flushes_immediately() -> None:
    debouncer = FeishuMessageDebouncer()
    seen: list[str] = []

    async def on_flush(merged: FeishuInboundText, ids: list[str]) -> None:
        seen.append(merged.text)

    await debouncer.schedule(_inbound("now"), debounce_ms=0, on_flush=on_flush)
    assert seen == ["now"]


@pytest.mark.asyncio
async def test_reset_clears_pending_buffers() -> None:
    debouncer = FeishuMessageDebouncer()
    flushed: list[str] = []

    async def on_flush(merged: FeishuInboundText, ids: list[str]) -> None:
        flushed.append(merged.text)

    await debouncer.schedule(_inbound("pending"), debounce_ms=500, on_flush=on_flush)
    await debouncer.reset()
    await asyncio.sleep(0.05)
    assert flushed == []


@pytest.mark.asyncio
async def test_reset_feishu_message_debouncer_singleton() -> None:
    await reset_feishu_message_debouncer()


def test_feishu_message_debounce_ms_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.feishu.message_debounce.get_config",
        lambda key, default=None: default,
    )
    assert feishu_message_debounce_ms() == 800
