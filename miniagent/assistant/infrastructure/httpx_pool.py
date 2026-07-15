"""Loop-scoped shared HTTPX connection pools for dynamically loaded tools."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_clients: dict[asyncio.AbstractEventLoop, Any] = {}
_clients_lock = threading.Lock()


async def get_shared_httpx_client() -> Any:
    """Return the current runtime loop's bounded, reusable HTTPX client.

    Skill modules are hot-reloaded and therefore must not own long-lived
    clients themselves. Keeping ownership here gives shutdown one stable place
    to close every pool while preserving per-event-loop transport affinity.
    """
    import httpx

    loop = asyncio.get_running_loop()
    stale_clients: list[Any] = []
    with _clients_lock:
        stale_loops = [cached_loop for cached_loop in _clients if cached_loop.is_closed()]
        for stale_loop in stale_loops:
            stale_clients.append(_clients.pop(stale_loop))
        client = _clients.get(loop)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
            _clients[loop] = client
    if stale_clients:
        await asyncio.gather(
            *(stale.aclose() for stale in stale_clients if not stale.is_closed),
            return_exceptions=True,
        )
    return client


async def close_shared_httpx_clients() -> None:
    """Close and forget all shared dynamic-tool HTTP connection pools."""
    with _clients_lock:
        clients = tuple(_clients.values())
        _clients.clear()
    if clients:
        await asyncio.gather(
            *(client.aclose() for client in clients if not client.is_closed),
            return_exceptions=True,
        )


__all__ = ["close_shared_httpx_clients", "get_shared_httpx_client"]
