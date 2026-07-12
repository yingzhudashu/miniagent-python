"""Construction and lifecycle helpers for the application-owned OpenAI client.

This module is deliberately stateless. The composition root creates one
``AsyncOpenAI`` instance and stores it on ``ApplicationContainer``. Runtime
code receives that instance explicitly, so importing a feature module can
never create a second client or hide a dependency behind a service locator.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncOpenAI

from miniagent.infrastructure.debug_ndjson import safe_agent_debug_log
from miniagent.infrastructure.json_config import get_config
from miniagent.types.config import normalize_wire_api

if TYPE_CHECKING:
    from miniagent.bootstrap.application import ApplicationContainer

_CONNECT_TIMEOUT_SEC = 30.0
_logger = logging.getLogger(__name__)


def _read_http_timeout(config_getter: Callable[[str, Any], Any] = get_config) -> float:
    raw = config_getter("agent.http_timeout", 120.0)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"agent.http_timeout must be numeric, got {raw!r}"
        ) from None
    if timeout <= 0:
        raise RuntimeError("agent.http_timeout must be greater than zero")
    return timeout


def _read_retry_count(config_getter: Callable[[str, Any], Any] = get_config) -> int:
    raw = config_getter("model.retry_count", 2)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"model.retry_count must be an integer, got {raw!r}") from None
    if count < 0:
        raise RuntimeError("model.retry_count must not be negative")
    return count


def _read_wire_api(config_getter: Callable[[str, Any], Any] = get_config) -> str:
    try:
        return normalize_wire_api(config_getter("model.wire_api", "chat_completions"))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None


def _read_user_agent(
    config_getter: Callable[[str, Any], Any] = get_config,
) -> str | None:
    value = str(config_getter("model.user_agent", "") or "").strip()
    if not value:
        return None
    if "\r" in value or "\n" in value:
        raise RuntimeError("model.user_agent must not contain CR or LF characters")
    return value


def create_async_openai_client(
    *,
    api_key: str | None = None,
    config_getter: Callable[[str, Any], Any] | None = None,
) -> AsyncOpenAI:
    """Build one client from an explicit or currently installed configuration."""
    getter = config_getter or get_config
    resolved_api_key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not resolved_api_key:
        safe_agent_debug_log(
            hypothesis_id="C",
            location="openai_client.py:create_async_openai_client",
            message="missing_openai_api_key_abort",
            data={},
        )
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Set secrets.openai_api_key in "
            "config.user.json before starting MiniAgent."
        )

    base_url = getter("model.base_url", None)
    wire_api = _read_wire_api(getter)
    user_agent = _read_user_agent(getter)
    client_kwargs: dict[str, Any] = dict(
        api_key=resolved_api_key,
        base_url=base_url,
        timeout=httpx.Timeout(
            _read_http_timeout(getter), connect=_CONNECT_TIMEOUT_SEC
        ),
        max_retries=_read_retry_count(getter),
    )
    if user_agent:
        client_kwargs["default_headers"] = {"User-Agent": user_agent}
    client = AsyncOpenAI(**client_kwargs)
    safe_agent_debug_log(
        hypothesis_id="A",
        location="openai_client.py:create_async_openai_client",
        message="async_openai_created",
        data={
            "base_url_nonempty": bool(str(base_url or "").strip()),
            "api_key_len": len(resolved_api_key),
            "wire_api": wire_api,
            "custom_user_agent": bool(user_agent),
        },
    )
    return client


async def close_async_openai_client(client: Any | None) -> None:
    """Close an owned client once; tolerate lightweight test doubles."""
    if client is None:
        return
    close = getattr(client, "close", None)
    if not callable(close):
        return
    result = close()
    if hasattr(result, "__await__"):
        await result


async def replace_async_openai_client(container: ApplicationContainer) -> AsyncOpenAI:
    """Atomically replace the container client after a configuration reload.

    Construction happens before mutating the container. Invalid new settings
    therefore leave the working client in place. Once installed, the previous
    client's connection pool is closed deterministically.
    """
    replacement = create_async_openai_client()
    return await install_async_openai_client(container, replacement)


async def install_async_openai_client(
    container: ApplicationContainer,
    replacement: AsyncOpenAI,
    *,
    retire_previous: bool = False,
) -> AsyncOpenAI:
    """Install a prevalidated client, closing or retiring the previous pool."""
    previous = container.openai_client
    container.openai_client = replacement
    if previous is None or previous is replacement:
        return replacement
    if retire_previous:
        container.retired_openai_clients.append(previous)
        return replacement
    try:
        await close_async_openai_client(previous)
    except Exception as error:
        _logger.warning("关闭旧 OpenAI 客户端失败: %s", error)
    return replacement


__all__ = [
    "close_async_openai_client",
    "create_async_openai_client",
    "install_async_openai_client",
    "replace_async_openai_client",
]
