"""Tests for the single application composition root."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.bootstrap.application import ApplicationContainer
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


def _make_container() -> ApplicationContainer:
    return ApplicationContainer(
        registry=MagicMock(),
        monitor=MagicMock(),
        skill_registry=MagicMock(),
        clawhub=MagicMock(),
        engine=MagicMock(),
        channel_router=MagicMock(),
        message_queue=MagicMock(),
        feishu=MagicMock(),
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
    )


def test_container_defaults_and_repr_hide_runtime_resources() -> None:
    container = _make_container()
    rendered = repr(container)

    assert container.llm_gateway is None
    assert container.config is None
    for field_name in (
        "create_feishu_handler_factory",
        "cli_transcript_append",
        "cli_transcript_coordinator",
        "shutdown_tracked_tasks",
    ):
        assert field_name not in rendered


@pytest.mark.asyncio
async def test_container_tracks_each_live_shutdown_task_once() -> None:
    container = _make_container()
    task = asyncio.create_task(asyncio.sleep(10))

    container.register_shutdown_tracked_task(task)
    container.register_shutdown_tracked_task(task)
    assert container.shutdown_tracked_tasks == {task}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert container.shutdown_tracked_tasks == set()


def test_importing_infrastructure_does_not_load_core_or_feishu() -> None:
    code = (
        "import sys; import miniagent.assistant.infrastructure; "
        "assert 'miniagent.agent' not in sys.modules; "
        "assert 'miniagent.assistant.feishu' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
