"""Shared HTTPX pool lifecycle tests for hot-reloaded skill tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from miniagent.assistant.infrastructure import httpx_pool


class _FakeClient:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.is_closed = False
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.is_closed = True


@pytest.mark.asyncio
async def test_shared_httpx_client_reuses_current_loop_and_closes_once() -> None:
    await httpx_pool.close_shared_httpx_clients()
    created: list[_FakeClient] = []

    def factory(**kwargs: object) -> _FakeClient:
        client = _FakeClient(**kwargs)
        created.append(client)
        return client

    with patch("httpx.AsyncClient", side_effect=factory):
        first = await httpx_pool.get_shared_httpx_client()
        second = await httpx_pool.get_shared_httpx_client()

    assert first is second
    assert len(created) == 1
    limits = created[0].kwargs["limits"]
    assert limits.max_connections == 20
    assert limits.max_keepalive_connections == 10

    await httpx_pool.close_shared_httpx_clients()
    assert created[0].close_calls == 1
    assert httpx_pool._clients == {}


@pytest.mark.asyncio
async def test_shared_httpx_client_retires_closed_loop_pool() -> None:
    await httpx_pool.close_shared_httpx_clients()
    stale = _FakeClient()

    class ClosedLoop:
        def is_closed(self) -> bool:
            return True

    httpx_pool._clients[ClosedLoop()] = stale  # type: ignore[index]
    created: list[_FakeClient] = []

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: created.append(_FakeClient(**kwargs)) or created[-1],
    ):
        current = await httpx_pool.get_shared_httpx_client()

    assert current is created[0]
    assert stale.is_closed
    assert stale.close_calls == 1
    await httpx_pool.close_shared_httpx_clients()
