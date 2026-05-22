"""Shared helpers for scheduled_tasks ticker tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.message_queue import MessageQueueManager


def patch_tick_once_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock cross-process locks so tick_once tests run without real lock files."""
    monkeypatch.setattr("miniagent.scheduled_tasks.ticker.try_acquire_scheduler_lock", lambda: True)
    monkeypatch.setattr("miniagent.scheduled_tasks.ticker.release_scheduler_lock", lambda: None)
    monkeypatch.setattr("miniagent.scheduled_tasks.ticker.try_acquire_job_lock", lambda _id: True)
    monkeypatch.setattr("miniagent.scheduled_tasks.ticker.release_job_lock", lambda _id: None)


def minimal_tick_ctx(*, engine: Any | None = None) -> SimpleNamespace:
    """Minimal RuntimeContext-like namespace for tick_once."""
    router = MagicMock()
    router.primary = "default"
    mq = MessageQueueManager()
    feishu_rt = MagicMock()
    feishu_rt.get_config.return_value = None
    return SimpleNamespace(
        message_queue=mq,
        channel_router=router,
        engine=engine or MagicMock(),
        registry=None,
        monitor=None,
        clawhub=None,
        memory_store=None,
        activity_log=None,
        keyword_index=None,
        openai_client=None,
        cli_transcript_append=None,
        feishu=feishu_rt,
    )


def minimal_cli_state(ctx: SimpleNamespace) -> CliLoopState:
    return {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }
