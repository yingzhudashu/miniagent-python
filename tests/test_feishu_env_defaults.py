"""飞书环境变量默认值与 ``env_flag_strict`` 行为。"""

from __future__ import annotations

import pytest


def test_feishu_reply_plain_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import _feishu_reply_plain_enabled

    monkeypatch.delenv("MINIAGENT_FEISHU_REPLY_PLAIN", raising=False)
    assert _feishu_reply_plain_enabled() is True
    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_PLAIN", "0")
    assert _feishu_reply_plain_enabled() is False


def test_feishu_reply_plain_typo_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import _feishu_reply_plain_enabled

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_PLAIN", "maybe")
    assert _feishu_reply_plain_enabled() is False


def test_openclaw_config_fallback(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from miniagent.runtime import external_config as ec

    ec.reset_external_config_for_tests()
    monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
    cfg = {
        "models": {"providers": {"p": {"baseUrl": "https://x/v1", "apiKey": "k"}}},
        "agents": {"defaults": {"model": {"primary": "p/m"}}},
    }
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MINIAGENT_OPENCLAW_CONFIG", str(p))
    patch = ec.load_external_config_from_env()
    assert patch.model == "m"


def test_folder_token_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.folder_token_resolve import default_doc_folder_token_from_env
    from miniagent.infrastructure.env_parse import reset_env_legacy_warnings_for_tests

    reset_env_legacy_warnings_for_tests()
    monkeypatch.delenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", raising=False)
    monkeypatch.setenv("FEISHU_DEFAULT_DOC_FOLDER_TOKEN", "fld_legacy")
    assert default_doc_folder_token_from_env() == "fld_legacy"
