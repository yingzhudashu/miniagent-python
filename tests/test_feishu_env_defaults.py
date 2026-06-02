"""飞书环境变量默认值与 ``env_flag_strict`` 行为。"""

from __future__ import annotations

import pytest


def test_feishu_reply_plain_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import _feishu_reply_plain_enabled

    monkeypatch.delenv("MINIAGENT_FEISHU_REPLY_PLAIN", raising=False)
    assert _feishu_reply_plain_enabled() is False
    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_PLAIN", "1")
    assert _feishu_reply_plain_enabled() is True


def test_feishu_reply_plain_typo_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import _feishu_reply_plain_enabled

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_PLAIN", "maybe")
    assert _feishu_reply_plain_enabled() is False
