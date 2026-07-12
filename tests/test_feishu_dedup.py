"""Tests for the instance-owned Feishu message deduplicator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniagent.feishu import feishu_dedup as dedup_module
from miniagent.feishu.feishu_dedup import FeishuDeduplicator


def test_claim_abandon_and_release(tmp_path: Path) -> None:
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.try_begin_processing("om_1")
    assert not dedup.try_begin_processing("om_1")
    dedup.abandon_processing_claim("om_1")
    assert dedup.try_begin_processing("om_1")
    dedup.release_processing("om_1")
    assert not dedup.try_begin_processing("om_1")


def test_blank_ids_are_not_persisted(tmp_path: Path) -> None:
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.try_begin_processing("")
    dedup.release_processing("   ")
    assert dedup.stats()["disk_dedup"] == 0


@pytest.mark.asyncio
async def test_close_persists_and_new_instance_loads_state(tmp_path: Path) -> None:
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.try_begin_processing("om_saved")
    dedup.release_processing("om_saved")
    await dedup.close()

    loaded = FeishuDeduplicator(str(tmp_path))
    assert not loaded.try_begin_processing("om_saved")
    data = json.loads(
        (tmp_path / "feishu" / "dedup" / "processed.json").read_text(
            encoding="utf-8"
        )
    )
    assert "mini-agent:om_saved" in data


def test_corrupt_state_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "feishu" / "dedup" / "processed.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.try_begin_processing("om_new")


def test_stats_do_not_expose_internal_state(tmp_path: Path) -> None:
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.stats() == {
        "processing_claims": 0,
        "disk_dedup": 0,
        "dirty": False,
        "state_dir": str(tmp_path / "feishu" / "dedup"),
    }


def test_completed_claim_expires_even_when_cache_is_small(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr(dedup_module.time, "time", lambda: now)
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.try_begin_processing("om_expiring")
    dedup.release_processing("om_expiring")
    assert not dedup.try_begin_processing("om_expiring")

    now += dedup_module.DEDUP_TTL_MS / 1000.0 + 1
    assert dedup.try_begin_processing("om_expiring")


@pytest.mark.asyncio
async def test_load_prunes_expired_persisted_ids(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "feishu" / "dedup" / "processed.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"mini-agent:old":100}', encoding="utf-8")
    monkeypatch.setattr(
        dedup_module.time,
        "time",
        lambda: 100 + dedup_module.DEDUP_TTL_MS / 1000.0 + 1,
    )

    dedup = FeishuDeduplicator(str(tmp_path))

    assert dedup.stats()["disk_dedup"] == 0
    assert dedup.try_begin_processing("old")
    dedup.abandon_processing_claim("old")
    await dedup.close()
    assert json.loads(target.read_text(encoding="utf-8")) == {}
