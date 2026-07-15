"""Provider-neutral gateway and role-routing contract tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from miniagent.contracts.llm import (
    LLMCompletion,
    LLMStreamEvent,
    ModelCapabilities,
    ModelDescriptor,
)
from miniagent.infrastructure.llm.catalog import ModelCatalog, RoleRouter
from miniagent.infrastructure.llm.gateway import LLMGateway, ProviderRegistry


class FauxProvider:
    provider_id = "faux"

    def __init__(self) -> None:
        self.requests: list[tuple[ModelDescriptor, dict[str, Any]]] = []
        self.closed = False

    async def list_models(self):
        return (
            ModelDescriptor(
                profile="faux:dynamic",
                provider="faux",
                model="dynamic",
                api="openai_chat",
            ),
        )

    async def create_completion(self, model, *, messages, params, tools=None, json_mode=False):
        self.requests.append((model, params))
        return LLMCompletion(content="ok", model=model.model)

    async def stream_completion(
        self, model, *, messages, params, tools=None, json_mode=False
    ) -> AsyncIterator[LLMStreamEvent]:
        self.requests.append((model, params))
        yield LLMStreamEvent(event_type="text_delta", content_delta="o")
        yield LLMStreamEvent(event_type="text_delta", content_delta="k")
        yield LLMStreamEvent(event_type="completed", completed=True)

    async def close(self) -> None:
        self.closed = True


def _gateway(*, vision: bool = True) -> tuple[LLMGateway, FauxProvider]:
    model = ModelDescriptor(
        profile="primary",
        provider="faux",
        model="answer-model",
        api="openai_chat",
        max_output_tokens=100,
        capabilities=ModelCapabilities(vision=vision),
    )
    catalog = ModelCatalog((model,))
    router = RoleRouter(
        catalog,
        {role: "primary" for role in ("default", "reasoning", "fast", "vision")},
    )
    provider = FauxProvider()
    return LLMGateway(ProviderRegistry((provider,)), catalog, router), provider


@pytest.mark.asyncio
async def test_gateway_routes_role_and_strips_internal_parameters() -> None:
    gateway, provider = _gateway()
    response = await gateway.create_completion(
        messages=[{"role": "user", "content": "hello"}],
        params={"model": "ignored", "_role": "reasoning", "max_tokens": 200},
    )
    assert response.content == "ok"
    model, params = provider.requests[0]
    assert model.profile == "primary"
    assert params == {"max_tokens": 100, "model": "answer-model"}


@pytest.mark.asyncio
async def test_gateway_normalizes_thinking_defaults_and_keeps_runtime_override() -> None:
    model = ModelDescriptor(
        profile="reasoner",
        provider="faux",
        model="reasoning-model",
        api="openai_responses",
        defaults={"thinking_level": "heavy", "thinking_budget": 81920},
    )
    catalog = ModelCatalog((model,))
    provider = FauxProvider()
    gateway = LLMGateway(
        ProviderRegistry((provider,)),
        catalog,
        RoleRouter(catalog, {"reasoning": "reasoner"}),
    )

    await gateway.create_completion(
        messages=[],
        params={"_role": "reasoning", "_thinking_level": "medium"},
    )

    assert provider.requests[0][1] == {
        "_thinking_level": "medium",
        "_thinking_budget": 81920,
        "model": "reasoning-model",
        "max_tokens": 4096,
    }


@pytest.mark.asyncio
async def test_model_compatibility_is_explicit_and_applied_before_provider() -> None:
    model = ModelDescriptor(
        profile="compat",
        provider="faux",
        model="compat-model",
        api="openai_chat",
        defaults={"temperature": 0.4},
        compatibility={
            "supports_temperature": False,
            "parameter_map": {"max_tokens": "max_completion_tokens"},
            "extra_body": {"enable_thinking": True},
        },
    )
    catalog = ModelCatalog((model,))
    provider = FauxProvider()
    gateway = LLMGateway(
        ProviderRegistry((provider,)),
        catalog,
        RoleRouter(catalog, {"default": "compat"}),
    )
    await gateway.create_completion(messages=[], params={"max_tokens": 10})
    assert provider.requests[0][1] == {
        "model": "compat-model",
        "max_completion_tokens": 10,
        "extra_body": {"enable_thinking": True},
    }


@pytest.mark.asyncio
async def test_gateway_stream_and_refresh_keep_normalized_events() -> None:
    gateway, _provider = _gateway()
    events = [
        event
        async for event in gateway.stream_completion(
            messages=[{"role": "user", "content": "hello"}],
            params={"_role": "default"},
        )
    ]
    assert "".join(event.content_delta or "" for event in events) == "ok"
    assert events[-1].completed is True
    await gateway.refresh("faux")
    assert gateway.catalog.get("faux:dynamic") is not None


@pytest.mark.asyncio
async def test_gateway_close_is_idempotent() -> None:
    gateway, provider = _gateway()
    await gateway.close()
    await gateway.close()
    assert provider.closed is True


def test_vision_role_rejects_incompatible_profile_before_request() -> None:
    gateway, _provider = _gateway(vision=False)
    with pytest.raises(ValueError, match="vision role"):
        gateway.model_for_role("vision")
