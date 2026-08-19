"""Tests for transactional channel-neutral inbound deduplication."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.assistant.infrastructure.inbound_dedup import InboundDeduplicator


def test_claim_complete_and_duplicate(tmp_path: Path) -> None:
    dedup = InboundDeduplicator("feishu", str(tmp_path))
    assert dedup.try_begin_processing("m1") is True
    assert dedup.try_begin_processing("m1") is False
    dedup.complete("m1")
    assert dedup.try_begin_processing("m1") is False
    assert dedup.stats()["disk_dedup"] == 1


def test_abandoned_claim_can_be_reclaimed(tmp_path: Path) -> None:
    dedup = InboundDeduplicator("feishu", str(tmp_path))
    assert dedup.try_begin_processing("m2") is True
    dedup.abandon("m2")
    assert dedup.try_begin_processing("m2") is True


def test_two_instances_have_one_live_claim_winner(tmp_path: Path) -> None:
    first = InboundDeduplicator("feishu", str(tmp_path))
    second = InboundDeduplicator("feishu", str(tmp_path))
    assert first.try_begin_processing("m3") is True
    assert second.try_begin_processing("m3") is False
    first.abandon("m3")
    assert second.try_begin_processing("m3") is True


@pytest.mark.asyncio
async def test_completion_survives_new_instance(tmp_path: Path) -> None:
    first = InboundDeduplicator("feishu", str(tmp_path))
    assert first.try_begin_processing("m4") is True
    first.complete("m4")
    await first.close()
    second = InboundDeduplicator("feishu", str(tmp_path))
    assert second.try_begin_processing("m4") is False


@pytest.mark.asyncio
async def test_close_releases_unfinished_claim(tmp_path: Path) -> None:
    first = InboundDeduplicator("feishu", str(tmp_path))
    assert first.try_begin_processing("m5") is True
    await first.close()
    second = InboundDeduplicator("feishu", str(tmp_path))
    assert second.try_begin_processing("m5") is True


def test_empty_message_id_is_not_persisted(tmp_path: Path) -> None:
    dedup = InboundDeduplicator("feishu", str(tmp_path))
    assert dedup.try_begin_processing("  ") is False
    assert dedup.stats() == {
        "processing_claims": 0,
        "disk_dedup": 0,
        "dirty": False,
        "state_dir": str(tmp_path),
    }
