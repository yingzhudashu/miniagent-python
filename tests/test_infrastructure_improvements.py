"""Tests for infrastructure metrics, caches and process locks."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from miniagent.agent import debug as debug_ndjson
from miniagent.assistant.infrastructure.feishu_inbound_lock import (
    read_feishu_inbound_owner,
    release_feishu_inbound_owner,
    try_acquire_feishu_inbound_owner,
)
from tests.config_helpers import install_test_config


def test_feishu_inbound_lock_acquire_and_release(tmp_path: Path) -> None:
    ok, msg = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=1)
    assert ok, msg
    owner = read_feishu_inbound_owner(state_dir=str(tmp_path))
    assert owner is not None
    assert owner.get("alive") is True
    release_feishu_inbound_owner(state_dir=str(tmp_path))
    assert read_feishu_inbound_owner(state_dir=str(tmp_path)) is None


def test_feishu_inbound_lock_blocks_second_live_pid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.is_process_running",
        lambda pid: pid > 0,
    )
    ok, _ = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=1)
    assert ok
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.os.getpid",
        lambda: 888_888,
    )
    ok2, msg2 = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=2)
    assert not ok2
    assert "占用" in msg2


def test_debug_ndjson_respects_reload_config(tmp_path: Path) -> None:
    install_test_config(tmp_path, {})
    importlib.reload(debug_ndjson)
    log_file = tmp_path / "debug-hot.log"
    debug_ndjson.agent_debug_log(hypothesis_id="H", location="t", message="before")
    assert not log_file.exists()

    install_test_config(
        tmp_path,
        {"debug": {"session_id": "hot", "log_path": str(log_file)}},
    )
    from miniagent.assistant.infrastructure.json_config import reload_config

    reload_config()
    debug_ndjson.agent_debug_log(hypothesis_id="H", location="t", message="after")
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "after"
