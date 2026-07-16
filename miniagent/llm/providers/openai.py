"""OpenAI and OpenAI-compatible provider implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx
from openai import AsyncOpenAI

from miniagent.llm.catalog import with_provider_profile
from miniagent.llm.types import (
    LLMCompletion,
    LLMStreamEvent,
    ModelCapabilities,
    ModelDescriptor,
    OpenAIWireAPI,
)


class OpenAIProvider:
    """One client for OpenAI or a compatible endpoint."""

    def __init__(
        self,
        provider_id: str,
        *,
        api_key: str,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self._provider_id = provider_id
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=dict(headers or {}),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 30.0)),
            max_retries=max_retries,
        )

    @property
    def provider_id(self) -> str:
        """返回注册表中的稳定 provider 标识。"""
        return self._provider_id

    async def list_models(self) -> Sequence[ModelDescriptor]:
        """Discover models explicitly; a failure leaves catalog state unchanged."""
        response = await self._client.models.list()
        result = []
        for item in getattr(response, "data", ()) or ():
            model_id = str(getattr(item, "id", "") or "").strip()
            if not model_id:
                continue
            result.append(
                with_provider_profile(
                    ModelDescriptor(
                        profile=model_id,
                        provider=self.provider_id,
                        model=model_id,
                        api="openai_chat",
                        capabilities=ModelCapabilities(),
                    ),
                    self.provider_id,
                    f"{self.provider_id}:{model_id}",
                )
            )
        return tuple(result)

    async def create_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        """通过选定 OpenAI wire API 执行非流式请求。"""
        from miniagent.llm.providers.openai_transport import create_completion

        wire_api: OpenAIWireAPI = (
            "responses" if model.api == "openai_responses" else "chat_completions"
        )
        return await create_completion(
            self._client,
            messages=messages,
            params=params,
            tools=tools,
            json_mode=json_mode,
            wire_api=wire_api,
        )

    async def stream_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        """通过选定 OpenAI wire API 产出共享流式事件。"""
        from miniagent.llm.providers.openai_transport import stream_completion

        wire_api: OpenAIWireAPI = (
            "responses" if model.api == "openai_responses" else "chat_completions"
        )
        async for event in stream_completion(
            self._client,
            messages=messages,
            params=params,
            tools=tools,
            json_mode=json_mode,
            wire_api=wire_api,
        ):
            yield event

    async def close(self) -> None:
        """关闭底层 AsyncOpenAI 连接池。"""
        await self._client.close()


__all__ = ["OpenAIProvider"]
