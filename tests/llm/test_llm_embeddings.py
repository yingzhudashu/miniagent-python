"""Provider-neutral LLM embedding transport contracts."""

from __future__ import annotations

import httpx
import pytest

from miniagent.llm import EmbeddingClient, EmbeddingConfig, EmbeddingRequest
from miniagent.llm.types import LLMTransportError


def config(**overrides) -> EmbeddingConfig:
    values = {
        "base_url": "https://embedding.example/v1",
        "model": "embed-model",
        "api_key": "test-key",
        "max_retries": 0,
        "backoff_factor": 0,
    }
    values.update(overrides)
    return EmbeddingConfig(**values)


@pytest.mark.asyncio
async def test_embedding_client_normalizes_vector_model_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://embedding.example/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.25, 0.75]}],
                "model": "resolved-model",
                "usage": {"prompt_tokens": 4, "ignored": "value"},
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = EmbeddingClient(config(), client=http)
    response = await client.create_embedding(EmbeddingRequest("hello"))

    assert response.embedding == (0.25, 0.75)
    assert response.model == "resolved-model"
    assert response.usage == {"prompt_tokens": 4}
    await client.close()
    assert client.closed is True
    assert http.is_closed is False
    await http.aclose()


@pytest.mark.asyncio
async def test_embedding_client_retries_transient_http_status() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = EmbeddingClient(config(max_retries=1), client=http)
    assert await client.embed("retry") == [1.0]
    assert attempts == 2
    await http.aclose()


@pytest.mark.asyncio
async def test_embedding_client_rejects_invalid_provider_shape() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"data": []}))
    )
    client = EmbeddingClient(config(), client=http)
    with pytest.raises(LLMTransportError, match="invalid response"):
        await client.embed("invalid")
    await http.aclose()


def test_embedding_configuration_is_validated_without_network_access() -> None:
    with pytest.raises(ValueError, match="base_url"):
        EmbeddingClient(config(base_url=""))
