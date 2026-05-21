"""CARD_V2 宽表第二张卡发送（mock）。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


def test_try_post_v2_wide_table_card(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import _try_post_v2_wide_table_card
    from miniagent.feishu.types import FeishuConfig

    monkeypatch.setenv("MINIAGENT_FEISHU_CARD_V2", "1")
    monkeypatch.setenv("MINIAGENT_FEISHU_LARK_TABLE_MAX_PIPES", "4")
    cfg = FeishuConfig(app_id="a", app_secret="b", verification_token="", encrypt_key="")
    md = "| a | b | c | d | e |\n|---|---|---|---|---|\n| 1 | 2 | 3 | 4 | 5 |"
    posted: list[str] = []

    def _capture(*_a, card_json: str, **_kw):
        posted.append(card_json)
        return True, "om_v2"

    with patch("miniagent.feishu.poll_server._post_interactive_message", side_effect=_capture):
        ok = _try_post_v2_wide_table_card(cfg, "oc_x", md, title_suffix="", reply_to_message_id=None, reply_in_thread=False)
    assert ok is True
    assert len(posted) == 1
    card = json.loads(posted[0])
    assert card.get("schema") == "2.0"
