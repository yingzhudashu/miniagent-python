"""Tests for explicitly owned OpenAI client construction and lifecycle."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.core.openai_client import (
    close_async_openai_client,
    create_async_openai_client,
    replace_async_openai_client,
)
from tests.config_helpers import install_test_config
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


def _minimal_container(client: object) -> ApplicationContainer:
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
        openai_client=client,
    )


def test_missing_api_key_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_async_openai_client()


def test_invalid_http_timeout_raises_runtime_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(tmp_path, {"agent": {"http_timeout": "invalid"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="agent.http_timeout"):
        create_async_openai_client()


def test_invalid_retry_count_raises_runtime_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(tmp_path, {"model": {"retry_count": "invalid"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="model.retry_count"):
        create_async_openai_client()


def test_invalid_wire_api_raises_runtime_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(tmp_path, {"model": {"wire_api": "legacy"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="model.wire_api"):
        create_async_openai_client()


@pytest.mark.parametrize("user_agent", ["bad\rvalue", "bad\nvalue"])
def test_user_agent_rejects_header_injection(
    tmp_path, monkeypatch: pytest.MonkeyPatch, user_agent: str
) -> None:
    install_test_config(tmp_path, {"model": {"user_agent": user_agent}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="CR or LF"):
        create_async_openai_client()


def test_constructor_receives_transport_configuration(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_test_config(
        tmp_path,
        {
            "model": {
                "base_url": "https://custom.example/v1",
                "retry_count": 5,
                "wire_api": "responses",
                "user_agent": "MiniAgent-Test",
            },
            "agent": {"http_timeout": 90},
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = MagicMock()

    with patch("miniagent.core.openai_client.AsyncOpenAI", return_value=client) as ctor:
        assert create_async_openai_client() is client

    kwargs = ctor.call_args.kwargs
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["base_url"] == "https://custom.example/v1"
    assert kwargs["max_retries"] == 5
    assert kwargs["timeout"].read == 90.0
    assert kwargs["timeout"].connect == 30.0
    assert kwargs["default_headers"] == {"User-Agent": "MiniAgent-Test"}


@pytest.mark.asyncio
async def test_close_async_openai_client_closes_owned_pool() -> None:
    client = MagicMock()
    client.close = AsyncMock()

    await close_async_openai_client(client)

    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_replace_installs_new_client_then_closes_previous() -> None:
    previous = MagicMock()
    previous.close = AsyncMock()
    replacement = MagicMock()
    container = _minimal_container(previous)

    with patch(
        "miniagent.core.openai_client.create_async_openai_client",
        return_value=replacement,
    ):
        result = await replace_async_openai_client(container)

    assert result is replacement
    assert container.openai_client is replacement
    assert container.retired_openai_clients == []
    previous.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_replace_failure_preserves_working_client() -> None:
    previous = MagicMock()
    previous.close = AsyncMock()
    container = _minimal_container(previous)

    with patch(
        "miniagent.core.openai_client.create_async_openai_client",
        side_effect=RuntimeError("invalid replacement"),
    ):
        with pytest.raises(RuntimeError, match="invalid replacement"):
            await replace_async_openai_client(container)

    assert container.openai_client is previous
    previous.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_keeps_new_client_when_old_close_fails() -> None:
    previous = MagicMock()
    previous.close = AsyncMock(side_effect=RuntimeError("close failed"))
    replacement = MagicMock()
    container = _minimal_container(previous)

    with patch(
        "miniagent.core.openai_client.create_async_openai_client",
        return_value=replacement,
    ):
        result = await replace_async_openai_client(container)

    assert result is replacement
    assert container.openai_client is replacement


@pytest.mark.asyncio
async def test_reload_runtime_config_replaces_explicit_container(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.infrastructure.json_config import reload_runtime_config

    install_test_config(tmp_path, {"secrets": {"openai_api_key": "sk-new"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-new")
    previous = MagicMock()
    previous.close = AsyncMock()
    replacement = MagicMock()
    container = _minimal_container(previous)

    with patch(
        "miniagent.core.openai_client.create_async_openai_client",
        return_value=replacement,
    ):
        await reload_runtime_config(container)

    assert container.openai_client is replacement
    assert container.retired_openai_clients == [previous]
    previous.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_invalid_json_preserves_config_and_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.infrastructure.json_config import (
        get_config,
        get_config_paths,
        reload_runtime_config,
    )

    install_test_config(
        tmp_path,
        {
            "model": {"model": "working-model"},
            "secrets": {"openai_api_key": "sk-working"},
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-working")
    previous = MagicMock()
    previous.close = AsyncMock()
    container = _minimal_container(previous)
    get_config_paths()[1].write_text("{invalid", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        await reload_runtime_config(container)

    assert get_config("model.model") == "working-model"
    assert container.openai_client is previous
    previous.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_invalid_client_settings_preserves_config_and_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.infrastructure.json_config import (
        get_config,
        get_config_paths,
        reload_runtime_config,
    )

    install_test_config(
        tmp_path,
        {
            "model": {"model": "working-model"},
            "secrets": {"openai_api_key": "sk-working"},
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-working")
    previous = MagicMock()
    previous.close = AsyncMock()
    container = _minimal_container(previous)
    get_config_paths()[1].write_text(
        json.dumps(
            {
                "agent": {"http_timeout": "invalid"},
                "model": {"model": "broken-model"},
                "secrets": {"openai_api_key": "sk-new"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="agent.http_timeout"):
        await reload_runtime_config(container)

    assert get_config("model.model") == "working-model"
    assert container.openai_client is previous
    previous.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_invalid_wire_api_preserves_config_and_client(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.infrastructure.json_config import (
        get_config,
        get_config_paths,
        reload_runtime_config,
    )

    install_test_config(
        tmp_path,
        {
            "model": {"model": "working-model", "wire_api": "chat_completions"},
            "secrets": {"openai_api_key": "sk-working"},
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-working")
    previous = MagicMock()
    previous.close = AsyncMock()
    container = _minimal_container(previous)
    get_config_paths()[1].write_text(
        json.dumps(
            {
                "model": {"model": "broken-model", "wire_api": "legacy"},
                "secrets": {"openai_api_key": "sk-new"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="model.wire_api"):
        await reload_runtime_config(container)

    assert get_config("model.model") == "working-model"
    assert container.openai_client is previous
    previous.close.assert_not_awaited()
