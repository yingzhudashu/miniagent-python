"""``UnifiedEngine.session_turn`` 串行边界回归测试。

修复背景：CLI 与飞书曾走两条互不串行的独立队列，同一 session_key 下整轮 turn
（问题块 + 执行 + 答案块）未原子化，导致输出交错（CLI问题+飞书问题+CLI答案+飞书答案）
且同一会话被两条队列各驱动一次。``session_turn(session_key)`` 把整轮纳入会话级锁，
同一 session 严格串行、原子；不同 session 仍可并行。
"""

from __future__ import annotations

import asyncio

import pytest

from miniagent.assistant.engine.engine import UnifiedEngine


@pytest.mark.asyncio
async def test_session_turn_same_session_atomic_no_interleave() -> None:
    """同一 session_key 的两个 turn（模拟 CLI + 飞书）整轮原子、不交错。"""
    engine = UnifiedEngine()
    engine._session_exec = engine._session_exec.__class__(
        parallel_sessions=True, max_parallel_sessions=4
    )
    blocks: list[str] = []

    async def turn(tag: str) -> None:
        async with engine.session_turn("default"):
            blocks.append(f"{tag}:问题")
            await asyncio.sleep(0.02)  # 模拟 run_agent
            blocks.append(f"{tag}:答案")

    await asyncio.gather(turn("cli"), turn("feishu"))

    # 整轮原子：问题块与对应答案块相邻，不出现「问题/问题/答案/答案」交错
    assert blocks in (
        ["cli:问题", "cli:答案", "feishu:问题", "feishu:答案"],
        ["feishu:问题", "feishu:答案", "cli:问题", "cli:答案"],
    )


@pytest.mark.asyncio
async def test_session_turn_different_sessions_parallel() -> None:
    """不同 session_key 的 turn 仍可并行（保留 parallel_sessions 能力）。"""
    engine = UnifiedEngine()
    engine._session_exec = engine._session_exec.__class__(
        parallel_sessions=True, max_parallel_sessions=4
    )
    in_flight = 0
    overlap = False

    async def turn(key: str) -> None:
        nonlocal in_flight, overlap
        async with engine.session_turn(key):
            in_flight += 1
            if in_flight >= 2:
                overlap = True
            await asyncio.sleep(0.05)
            in_flight -= 1

    await asyncio.gather(turn("sess-a"), turn("sess-b"))
    assert overlap is True


@pytest.mark.asyncio
async def test_session_turn_serializes_each_message_once() -> None:
    """同一 session 下两条消息各执行一次（不重复驱动 → 不双重评估）。"""
    engine = UnifiedEngine()
    engine._session_exec = engine._session_exec.__class__(
        parallel_sessions=True, max_parallel_sessions=4
    )
    exec_count = 0
    concurrent = 0
    max_concurrent = 0

    async def turn() -> None:
        nonlocal exec_count, concurrent, max_concurrent
        async with engine.session_turn("default"):
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            exec_count += 1
            await asyncio.sleep(0.02)
            concurrent -= 1

    await asyncio.gather(turn(), turn())
    # 两条消息各执行一次，且任意时刻仅一个在跑（同 session 串行）
    assert exec_count == 2
    assert max_concurrent == 1
