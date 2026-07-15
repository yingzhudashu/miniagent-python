"""Deprecated v2 import surface; new runtime code uses ``LLMGateway``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from miniagent.infrastructure.llm import openai_client as _legacy


def create_async_openai_client(
    *,
    api_key: str | None = None,
    config_getter: Callable[[str, Any], Any] | None = None,
) -> AsyncOpenAI:
    """Delegate while retaining the v2 patchable constructor test surface."""
    _legacy.AsyncOpenAI = AsyncOpenAI
    return _legacy.create_async_openai_client(
        api_key=api_key, config_getter=config_getter
    )


async def close_async_openai_client(client: Any | None) -> None:
    await _legacy.close_async_openai_client(client)


async def install_async_openai_client(
    container: Any,
    replacement: AsyncOpenAI,
    *,
    retire_previous: bool = False,
) -> AsyncOpenAI:
    return await _legacy.install_async_openai_client(
        container, replacement, retire_previous=retire_previous
    )


async def replace_async_openai_client(container: Any) -> AsyncOpenAI:
    replacement = create_async_openai_client()
    return await install_async_openai_client(container, replacement)


__all__ = [
    "AsyncOpenAI",
    "close_async_openai_client",
    "create_async_openai_client",
    "install_async_openai_client",
    "replace_async_openai_client",
]
