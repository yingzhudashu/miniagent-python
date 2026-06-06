"""SessionExecCoordinator 并行会话锁测试。"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.engine.session_exec import SessionExecCoordinator


@pytest.mark.asyncio
async def test_parallel_sessions_allow_different_keys() -> None:
    coord = SessionExecCoordinator(parallel_sessions=True, max_parallel_sessions=4)
    order: list[str] = []
    in_flight = 0
    overlap = False

    async def work(key: str) -> None:
        nonlocal in_flight, overlap
        async with coord.acquire(key):
            order.append(f"start:{key}")
            in_flight += 1
            if in_flight >= 2:
                overlap = True
            await asyncio.sleep(0.05)
            in_flight -= 1
            order.append(f"end:{key}")

    await asyncio.gather(work("a"), work("b"))
    assert overlap is True
    assert order.count("start:a") == 1
    assert order.count("start:b") == 1


@pytest.mark.asyncio
async def test_same_session_serial() -> None:
    coord = SessionExecCoordinator(parallel_sessions=True, max_parallel_sessions=4)
    order: list[str] = []

    async def work(tag: str) -> None:
        async with coord.acquire("same"):
            order.append(f"start:{tag}")
            await asyncio.sleep(0.03)
            order.append(f"end:{tag}")

    await asyncio.gather(work("1"), work("2"))
    assert order == ["start:1", "end:1", "start:2", "end:2"]


@pytest.mark.asyncio
async def test_global_serial_when_disabled() -> None:
    coord = SessionExecCoordinator(parallel_sessions=False, max_parallel_sessions=4)
    order: list[str] = []

    async def work(key: str) -> None:
        async with coord.acquire(key):
            order.append(f"start:{key}")
            await asyncio.sleep(0.03)
            order.append(f"end:{key}")

    await asyncio.gather(work("a"), work("b"))
    assert order == ["start:a", "end:a", "start:b", "end:b"]


@pytest.mark.asyncio
async def test_max_parallel_sessions_limit() -> None:
    coord = SessionExecCoordinator(parallel_sessions=True, max_parallel_sessions=2)
    running = 0
    max_seen = 0

    async def work(key: str) -> None:
        nonlocal running, max_seen
        async with coord.acquire(key):
            running += 1
            max_seen = max(max_seen, running)
            await asyncio.sleep(0.05)
            running -= 1

    await asyncio.gather(work("a"), work("b"), work("c"))
    assert max_seen <= 2
