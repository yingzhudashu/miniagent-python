"""Provider-neutral OpenAI-compatible embedding transport."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

import httpx

from miniagent.llm.types import ErrorCategory, LLMTransportError


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Immutable connection snapshot for an embedding model."""

    base_url: str
    model: str
    api_key: str
    timeout: float = 15.0
    max_retries: int = 3
    backoff_factor: float = 1.0

    def validate(self) -> None:
        """Reject incomplete connection settings and invalid retry limits."""
        if not self.base_url.strip() or not self.model.strip() or not self.api_key.strip():
            raise ValueError("embedding requires base_url, model and api_key")
        if self.timeout <= 0:
            raise ValueError("embedding timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("embedding max_retries must not be negative")
        if self.backoff_factor < 0:
            raise ValueError("embedding backoff_factor must not be negative")


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """One embedding input; batching can be added without exposing HTTP shapes."""

    text: str
    model: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingResponse:
    """Normalized vector plus optional provider usage metadata."""

    embedding: tuple[float, ...]
    model: str
    usage: dict[str, int]


class EmbeddingClient:
    """Reusable embedding client with bounded transient-error recovery."""

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout)
        self._owns_client = client is None
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this client has permanently released its owned transport."""
        return self._closed

    async def create_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Validate, send and normalize one embedding request."""
        if self._closed:
            raise RuntimeError("EmbeddingClient is closed")
        text = request.text.strip()
        if not text:
            raise ValueError("embedding input must not be empty")
        model = (request.model or self.config.model).strip()
        if not model:
            raise ValueError("embedding model must not be empty")
        response = await self._request(text, model)
        return self._normalize(response, model)

    async def embed(self, text: str, *, model: str | None = None) -> list[float]:
        """Convenience API used by Agent RAG providers."""
        response = await self.create_embedding(EmbeddingRequest(text, model))
        return list(response.embedding)

    async def _request(self, text: str, model: str) -> httpx.Response:
        url = self.config.base_url.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                response = await self._client.post(
                    url,
                    json={"model": model, "input": text},
                    headers=headers,
                    timeout=self.config.timeout,
                )
                response.raise_for_status()
                return response
            except asyncio.CancelledError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                retry = attempt + 1 < attempts
                if not retry:
                    raise LLMTransportError(
                        f"embedding transport failed: {error}",
                        category="timeout",
                        retryable=True,
                    ) from error
            except httpx.HTTPStatusError as error:
                retry = attempt + 1 < attempts and self._retryable(error.response.status_code)
                if not retry:
                    status_code = error.response.status_code
                    category: ErrorCategory = (
                        "authentication"
                        if status_code in {401, 403}
                        else "rate_limit"
                        if status_code == 429
                        else "unknown"
                    )
                    raise LLMTransportError(
                        f"embedding request failed with HTTP {status_code}",
                        category=category,
                        status_code=status_code,
                        retryable=self._retryable(status_code),
                    ) from error
            await asyncio.sleep(self.config.backoff_factor * (2**attempt))
        raise AssertionError("embedding retry loop exhausted")

    @staticmethod
    def _retryable(status_code: int) -> bool:
        return status_code in {408, 409, 429} or status_code >= 500

    @staticmethod
    def _normalize(response: httpx.Response, model: str) -> EmbeddingResponse:
        try:
            payload: Any = response.json()
            raw_vector = payload["data"][0]["embedding"]
            vector = tuple(float(value) for value in raw_vector)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMTransportError(
                "embedding provider returned an invalid response",
                category="unknown",
            ) from error
        if not vector:
            raise LLMTransportError(
                "embedding provider returned an empty vector",
                category="unknown",
            )
        if not all(math.isfinite(value) for value in vector):
            raise LLMTransportError(
                "embedding provider returned a non-finite vector",
                category="unknown",
            )
        raw_usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
        usage = {
            str(key): int(value)
            for key, value in raw_usage.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        return EmbeddingResponse(vector, str(payload.get("model") or model), usage)

    async def close(self) -> None:
        """Idempotently close the internally created HTTP client."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> EmbeddingClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()


__all__ = [
    "EmbeddingClient",
    "EmbeddingConfig",
    "EmbeddingRequest",
    "EmbeddingResponse",
]
