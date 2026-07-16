"""Tests for the single production entrypoint and composition root."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.infrastructure.message_queue import QueueMode


def _run_entrypoint_with_mock_runtime(*, queue_mode: str | None = None) -> ApplicationContainer:
    from miniagent.assistant.infrastructure import json_config as config_module

    real_get_config = config_module.get_config

    def get_config(key: str, default: object = None) -> object:
        if key == "agent.queue_mode" and queue_mode is not None:
            return queue_mode
        return real_get_config(key, default)

    with patch.object(config_module, "get_config", side_effect=get_config):
        from miniagent.assistant.bootstrap.entrypoint import create_application_container

        return create_application_container()


def test_entrypoint_builds_complete_application_container(
    state_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-entrypoint-key")
    container = _run_entrypoint_with_mock_runtime()

    assert isinstance(container, ApplicationContainer)
    assert container.registry is not None
    assert container.monitor is not None
    assert container.engine is not None
    assert container.message_queue is not None
    assert container.channel_router is not None
    assert container.feishu is not None
    assert container.memory.store is not None
    assert container.memory.activity_log is not None
    assert container.memory.keyword_index is not None
    assert container.outbound_channels is not None
    assert container.outbound_channels.list_channel_ids() == ()
    assert container.llm_gateway is not None


def test_entrypoint_applies_preemptive_queue_mode(
    state_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-entrypoint-key")
    container = _run_entrypoint_with_mock_runtime(queue_mode="preemptive")
    assert container.message_queue.mode is QueueMode.PREEMPTIVE


def test_entrypoint_rejects_unknown_queue_mode_with_default(
    state_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-entrypoint-key")
    with patch("miniagent.assistant.bootstrap.entrypoint._logger") as logger:
        container = _run_entrypoint_with_mock_runtime(queue_mode="invalid")
    assert container.message_queue.mode is QueueMode.QUEUE
    logger.warning.assert_called_once()
