"""飞书同卡追加工具行：多行 / 代码块保留 Markdown。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent.assistant.feishu.poll_server import append_feishu_thinking_same_card
from miniagent.assistant.feishu.types import FeishuConfig


@pytest.mark.asyncio
async def test_append_feishu_thinking_same_card_multiline_keeps_newlines_and_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.feishu.thinking_delivery._patch_interactive_thinking_message",
        lambda *_a, **_k: True,
    )

    cfg = FeishuConfig(app_id="test_app", app_secret="test_secret")
    st = SimpleNamespace(
        feishu_stream_accumulated="",
        feishu_thinking_message_id="msg_1",
        feishu_tool_section_started=False,
    )
    tool_line = "摘要\n\n```text\nhello\nworld\n```"
    await append_feishu_thinking_same_card(cfg, "oc_testchat", tool_line, "gray", st)
    acc = st.feishu_stream_accumulated
    assert "\n" in acc
    assert "```" in acc
