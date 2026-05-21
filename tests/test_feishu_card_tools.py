from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


@pytest.mark.asyncio
async def test_send_interactive_card_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_card_tools import _feishu_send_interactive_card
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")

    with patch(
        "miniagent.feishu.im_send.post_im_message",
        return_value=(True, "om_x", None),
    ):        r = await _feishu_send_interactive_card(
            {"markdown_body": "hi", "receive_id": "oc_test"},
            ToolContext(cwd="/tmp", message_queue_abort_chat_id="oc_test"),
        )
    assert r.success is True
    assert "om_x" in r.content
