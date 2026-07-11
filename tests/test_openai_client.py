"""Tests for shared AsyncOpenAI factory and injectable planner client."""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from miniagent.runtime.context import RuntimeContext

from miniagent.core.openai_client import (
    get_shared_async_openai,
    invalidate_shared_async_openai,
    reset_shared_async_openai_for_tests,
    sync_runtime_context_openai_client,
)
from tests.config_helpers import install_test_config


def _minimal_ctx() -> RuntimeContext:
    from unittest.mock import MagicMock

    from miniagent.runtime.context import RuntimeContext

    return RuntimeContext(
        registry=MagicMock(),
        monitor=MagicMock(),
        skill_registry=MagicMock(),
        clawhub=MagicMock(),
        engine=MagicMock(),
        channel_router=MagicMock(),
        message_queue=MagicMock(),
        feishu=MagicMock(),
        memory_store=MagicMock(),
        activity_log=MagicMock(),
        keyword_index=MagicMock(),
        memory_context=MagicMock(),
    )


def test_get_shared_async_openai_singleton() -> None:
    reset_shared_async_openai_for_tests()
    os.environ["OPENAI_API_KEY"] = "sk-test-placeholder-for-singleton"
    try:
        a = get_shared_async_openai()
        b = get_shared_async_openai()
        assert a is b
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


def test_missing_api_key_raises_runtime_error() -> None:
    reset_shared_async_openai_for_tests()
    os.environ.pop("OPENAI_API_KEY", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_shared_async_openai()


def test_invalid_http_timeout_raises_runtime_error(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    install_test_config(tmp_path, {"agent": {"http_timeout": "not-a-number"}})
    os.environ["OPENAI_API_KEY"] = "sk-test"
    try:
        with pytest.raises(RuntimeError, match="agent.http_timeout"):
            get_shared_async_openai()
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


def test_invalid_retry_count_raises_runtime_error(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    install_test_config(tmp_path, {"model": {"retry_count": "bad"}})
    os.environ["OPENAI_API_KEY"] = "sk-test"
    try:
        with pytest.raises(RuntimeError, match="model.retry_count"):
            get_shared_async_openai()
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


def test_async_openai_constructor_receives_timeout_and_retries(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    install_test_config(
        tmp_path,
        {
            "model": {
                "base_url": "https://custom.example/v1",
                "retry_count": 5,
            },
            "agent": {"http_timeout": 90},
        },
    )
    os.environ["OPENAI_API_KEY"] = "sk-test"
    created: list[MagicMock] = []

    def _fake_async_openai(**kwargs):
        client = MagicMock()
        client.close = AsyncMock()
        client._ctor_kwargs = kwargs
        created.append(client)
        return client

    try:
        with patch("miniagent.core.openai_client.AsyncOpenAI", side_effect=_fake_async_openai):
            client = get_shared_async_openai()
        assert client is created[0]
        kwargs = created[0]._ctor_kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["base_url"] == "https://custom.example/v1"
        assert kwargs["max_retries"] == 5
        assert kwargs["timeout"].read == 90.0
        assert kwargs["timeout"].connect == 30.0
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


def test_invalidate_rebuilds_new_client_instance(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    install_test_config(tmp_path, {"model": {"base_url": "https://first.example/v1"}})
    os.environ["OPENAI_API_KEY"] = "sk-first"
    created: list[MagicMock] = []

    def _fake_async_openai(**kwargs):
        client = MagicMock()
        client.close = AsyncMock()
        client._ctor_kwargs = kwargs
        created.append(client)
        return client

    try:
        with patch("miniagent.core.openai_client.AsyncOpenAI", side_effect=_fake_async_openai):
            first = get_shared_async_openai()
            invalidate_shared_async_openai()
            install_test_config(tmp_path, {"model": {"base_url": "https://second.example/v1"}})
            os.environ["OPENAI_API_KEY"] = "sk-second"
            second = get_shared_async_openai()
        assert first is not second
        assert len(created) == 2
        assert created[0]._ctor_kwargs["api_key"] == "sk-first"
        assert created[1]._ctor_kwargs["api_key"] == "sk-second"
        assert created[1]._ctor_kwargs["base_url"] == "https://second.example/v1"
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


@pytest.mark.asyncio
async def test_invalidate_schedules_client_close_when_loop_running() -> None:
    reset_shared_async_openai_for_tests()
    os.environ["OPENAI_API_KEY"] = "sk-test"
    client = MagicMock()
    client.close = AsyncMock()
    try:
        with patch("miniagent.core.openai_client.AsyncOpenAI", return_value=client):
            get_shared_async_openai()
        invalidate_shared_async_openai()
        await asyncio.sleep(0)
        client.close.assert_awaited_once()
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()


def test_sync_runtime_context_openai_client_updates_ctx(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    from miniagent.runtime.context import reset_runtime_context_for_tests, set_runtime_context

    install_test_config(
        tmp_path,
        {
            "secrets": {"openai_api_key": "sk-ctx-old"},
            "model": {"base_url": "https://old.example/v1"},
        },
    )
    os.environ["OPENAI_API_KEY"] = "sk-ctx-old"
    created: list[MagicMock] = []

    def _fake_async_openai(**kwargs):
        mock = MagicMock()
        mock.close = AsyncMock()
        mock._ctor_kwargs = kwargs
        created.append(mock)
        return mock

    ctx = _minimal_ctx()
    old_client = MagicMock(name="stale")
    ctx.openai_client = old_client
    set_runtime_context(ctx)

    try:
        with patch("miniagent.core.openai_client.AsyncOpenAI", side_effect=_fake_async_openai):
            first = get_shared_async_openai()
            ctx.openai_client = first
            install_test_config(
                tmp_path,
                {
                    "secrets": {"openai_api_key": "sk-ctx-new"},
                    "model": {"base_url": "https://new.example/v1"},
                },
            )
            os.environ["OPENAI_API_KEY"] = "sk-ctx-new"
            sync_runtime_context_openai_client()
        assert ctx.openai_client is not first
        assert ctx.openai_client is created[-1]
        assert created[-1]._ctor_kwargs["api_key"] == "sk-ctx-new"
        assert created[-1]._ctor_kwargs["base_url"] == "https://new.example/v1"
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()
        reset_runtime_context_for_tests()


def test_sync_runtime_context_clears_client_when_key_missing(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    from miniagent.runtime.context import reset_runtime_context_for_tests, set_runtime_context

    install_test_config(tmp_path, {"secrets": {"openai_api_key": "sk-ctx"}})
    os.environ["OPENAI_API_KEY"] = "sk-ctx"
    ctx = _minimal_ctx()
    set_runtime_context(ctx)

    try:
        with patch("miniagent.core.openai_client.AsyncOpenAI", return_value=MagicMock(close=AsyncMock())):
            ctx.openai_client = get_shared_async_openai()
        os.environ.pop("OPENAI_API_KEY", None)
        sync_runtime_context_openai_client()
        assert ctx.openai_client is None
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()
        reset_runtime_context_for_tests()


def test_reload_runtime_config_refreshes_runtime_context(tmp_path) -> None:
    reset_shared_async_openai_for_tests()
    from miniagent.infrastructure.json_config import reload_runtime_config
    from miniagent.runtime.context import reset_runtime_context_for_tests, set_runtime_context

    install_test_config(
        tmp_path,
        {
            "secrets": {"openai_api_key": "sk-reload-old"},
            "model": {"base_url": "https://reload-old.example/v1"},
        },
    )
    os.environ["OPENAI_API_KEY"] = "sk-reload-old"
    created: list[MagicMock] = []

    def _fake_async_openai(**kwargs):
        mock = MagicMock()
        mock.close = AsyncMock()
        mock._ctor_kwargs = kwargs
        created.append(mock)
        return mock

    ctx = _minimal_ctx()
    set_runtime_context(ctx)

    try:
        with patch("miniagent.core.openai_client.AsyncOpenAI", side_effect=_fake_async_openai):
            ctx.openai_client = get_shared_async_openai()
            first = ctx.openai_client
            install_test_config(
                tmp_path,
                {
                    "secrets": {"openai_api_key": "sk-reload-new"},
                    "model": {"base_url": "https://reload-new.example/v1"},
                },
            )
            reload_runtime_config()
        assert ctx.openai_client is not first
        assert created[-1]._ctor_kwargs["api_key"] == "sk-reload-new"
        assert created[-1]._ctor_kwargs["base_url"] == "https://reload-new.example/v1"
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        reset_shared_async_openai_for_tests()
        reset_runtime_context_for_tests()


@pytest.mark.asyncio
async def test_generate_plan_accepts_injected_client() -> None:
    from miniagent.core.planner import generate_plan
    from miniagent.types.tool import Toolbox

    reset_shared_async_openai_for_tests()
    valid_json = json.dumps(
        {
            "summary": "t",
            "steps": [],
            "requiredToolboxes": [],
            "suggestedConfig": {},
            "estimatedTokens": {},
            "contextStrategy": {},
            "requiresConfirmation": False,
            "riskLevel": "low",
            "estimatedCost": {},
            "outputSpec": {},
            "fallbackPlan": {},
        }
    )
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=valid_json))]
    mock_response.usage = None

    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=mock_response)

    plan = await generate_plan(
        "hello",
        [Toolbox(id="t", name="T", description="d")],
        client=client,
    )
    assert plan.summary == "t"
    client.chat.completions.create.assert_awaited()
