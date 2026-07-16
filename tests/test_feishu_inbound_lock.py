"""Process ownership tests for Feishu inbound delivery."""

from __future__ import annotations

from miniagent.assistant.infrastructure.feishu_inbound_lock import (
    read_feishu_inbound_owner,
    release_feishu_inbound_owner,
    try_acquire_feishu_inbound_owner,
)


def test_feishu_inbound_lock_acquire_and_release(tmp_path) -> None:
    acquired, message = try_acquire_feishu_inbound_owner(
        state_dir=str(tmp_path),
        instance_id=1,
    )

    assert acquired, message
    owner = read_feishu_inbound_owner(state_dir=str(tmp_path))
    assert owner is not None
    assert owner.get("alive") is True

    release_feishu_inbound_owner(state_dir=str(tmp_path))
    assert read_feishu_inbound_owner(state_dir=str(tmp_path)) is None


def test_feishu_inbound_lock_blocks_second_live_pid(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.is_process_running",
        lambda pid: pid > 0,
    )
    acquired, _ = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=1)
    assert acquired
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.os.getpid",
        lambda: 888_888,
    )

    acquired_again, message = try_acquire_feishu_inbound_owner(
        state_dir=str(tmp_path),
        instance_id=2,
    )

    assert acquired_again is False
    assert message
