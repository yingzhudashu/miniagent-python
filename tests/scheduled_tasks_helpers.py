"""Shared helpers for scheduled_tasks ticker tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.application.messaging import ChannelRegistry
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.infrastructure.message_queue import MessageQueueManager
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


def patch_tick_once_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock cross-process locks so tick_once tests run without real lock files."""
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.ticker.try_acquire_scheduler_lock", lambda: True)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.ticker.release_scheduler_lock", lambda: None)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.ticker.try_acquire_job_lock", lambda _id: True)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.ticker.release_job_lock", lambda _id: None)


def minimal_tick_ctx(*, engine: Any | None = None) -> SimpleNamespace:
    """Minimal ApplicationContainer-like namespace for ticker and runner tests.

    字段须与 :class:`~miniagent.assistant.bootstrap.application.ApplicationContainer` 保持同步，
    否则 runner 在访问缺失属性时会失败。
    """
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
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
        llm_gateway=None,
        outbound_channels=ChannelRegistry(),
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
