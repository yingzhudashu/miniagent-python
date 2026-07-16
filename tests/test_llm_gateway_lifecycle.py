"""Tests for provider-neutral gateway ownership and atomic hot reload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.assistant.bootstrap.application import ApplicationContainer
from tests.config_helpers import install_test_config
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


def _minimal_container(gateway: object | None) -> ApplicationContainer:
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
        llm_gateway=gateway,
    )


@pytest.mark.asyncio
async def test_reload_atomically_replaces_and_retires_gateway(tmp_path) -> None:
    from miniagent.assistant.infrastructure.json_config import reload_runtime_config

    install_test_config(tmp_path, {"llm": {"models": {"primary": {"model": "new-model"}}}})
    previous = MagicMock()
    replacement = MagicMock()
    container = _minimal_container(previous)

    with patch("miniagent.llm.factory.create_llm_gateway", return_value=replacement):
        await reload_runtime_config(container)

    assert container.llm_gateway is replacement
    assert container.retired_llm_gateways == [previous]


@pytest.mark.asyncio
async def test_reload_invalid_json_preserves_gateway(tmp_path) -> None:
    from miniagent.assistant.infrastructure.json_config import (
        get_config_paths,
        reload_runtime_config,
    )

    install_test_config(
        tmp_path, {"llm": {"models": {"primary": {"model": "working-model"}}}}
    )
    previous = MagicMock()
    container = _minimal_container(previous)
    get_config_paths()[1].write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        await reload_runtime_config(container)

    assert container.llm_gateway is previous
    assert container.retired_llm_gateways == []


@pytest.mark.asyncio
async def test_gateway_shutdown_closes_active_and_retired_once() -> None:
    from miniagent.assistant.engine.shutdown import _close_llm_gateways

    active = MagicMock()
    active.close = AsyncMock()
    retired = MagicMock()
    retired.close = AsyncMock()
    container = _minimal_container(active)
    container.retired_llm_gateways.extend((retired, active))

    await _close_llm_gateways(container)

    active.close.assert_awaited_once_with()
    retired.close.assert_awaited_once_with()
    assert container.llm_gateway is None
    assert container.retired_llm_gateways == []


def test_container_exposes_only_provider_neutral_gateway() -> None:
    gateway = object()
    container = _minimal_container(gateway)

    assert container.llm_client is gateway
    assert not hasattr(container, "openai_client")
