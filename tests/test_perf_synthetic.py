"""合成性能冒烟：Mock LLM + 本地路径（默认 CI 可跑，阈值宽松）。"""

from __future__ import annotations

import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.executor import execute_plan
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.memory.keyword_index import KeywordIndex
from miniagent.memory.store import DefaultMemoryStore
from miniagent.types.config import AgentConfig
from miniagent.types.memory import MemoryEntryInput, SessionMemory
from miniagent.types.planning import StructuredPlan
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult
from tests.perf_helpers import median_wall_seconds_async


@pytest.mark.perf
@pytest.mark.asyncio
async def test_s1_execute_plan_mock_median_under_cap() -> None:
    """S1：单工具 + 收尾文本；本地 wall time 上界（宽松，防灾难性退化）。"""
    main = DefaultToolRegistry()
    sess = DefaultToolRegistry()

    async def fake_handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(True, "ok")

    ping_schema = {
        "type": "function",
        "function": {
            "name": "ping_tool",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    sess.register(
        "ping_tool",
        ToolDefinition(
            schema=ping_schema,
            handler=fake_handler,
            permission="allowlist",
            help_text="",
            toolbox=None,
        ),
    )

    plan = StructuredPlan(summary="s", steps=[], required_toolboxes=[])

    mock_client = MagicMock()

    class _Chunk:
        def __init__(self, delta, usage=None):
            self.choices = [SimpleNamespace(delta=delta)]
            self.usage = usage

    call_count = {"n": 0}

    async def create_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:

            async def stream1():
                delta = SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call_1",
                            function=SimpleNamespace(name="ping_tool", arguments="{}"),
                        )
                    ],
                )
                yield _Chunk(delta)

            return stream1()

        async def stream2():
            yield _Chunk(SimpleNamespace(content="done", tool_calls=None))

        return stream2()

    mock_client.chat.completions.create = AsyncMock(side_effect=create_side_effect)

    ac = AgentConfig(
        max_turns=5,
        session_key=None,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_registry=sess,
    )

    ms = MagicMock()
    al = MagicMock()
    ki = MagicMock()
    ki.get_stats.return_value = {"total_keywords": 0}

    async def _once():
        call_count["n"] = 0
        out = await execute_plan(
            plan,
            "hi",
            main,
            MagicMock(),
            ac,
            client=mock_client,
            memory_store=ms,
            activity_log=al,
            keyword_index=ki,
        )
        assert "done" in out

    med = await median_wall_seconds_async(5, _once)
    assert med < 5.0, f"S1 median wall too high: {med:.3f}s"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_s2_keyword_index_single_save_after_batch_add() -> None:
    """S2：多次 add_entry 仅 flush 一次时 KeywordIndex.save 调用次数为 1。"""
    from datetime import datetime, timezone

    saves: list[int] = []

    with tempfile.TemporaryDirectory() as tmp:
        ki = KeywordIndex(state_dir=tmp)
        real_save = ki.save

        def counting_save() -> None:
            saves.append(1)
            return real_save()

        ki.save = counting_save  # type: ignore[method-assign]

        store = DefaultMemoryStore(state_dir=tmp, keyword_index=ki)
        sid = "s-perf"
        now = datetime.now(timezone.utc).isoformat()
        mem = SessionMemory(
            session_id=sid,
            cumulative_summary="",
            key_facts=[],
            entries=[],
            total_turns=0,
            first_seen=now,
            last_active=now,
        )
        await store.save(mem)

        for i in range(3):
            await store.add_entry(
                sid,
                MemoryEntryInput(
                    timestamp=now,
                    user_snippet=f"u{i}",
                    summary=f"s{i}",
                    facts=[],
                ),
            )

        assert len(saves) == 0
        store.flush_keyword_index()
        assert len(saves) == 1


@pytest.mark.perf
def test_s3_context_manager_estimate_bounded() -> None:
    """S3：较多工具 schema 下 token 估算可在合理时间内完成。"""
    import time

    from miniagent.memory.context import DefaultContextManager

    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x" * 200,
                "parameters": {"type": "object", "properties": {f"p{j}": {"type": "string"} for j in range(8)}},
            },
        }
        for i in range(40)
    ]

    cm = DefaultContextManager(
        context_window=128000,
        compress_threshold=0.99,
        tools=tools,
        overflow_strategy="summarize",
    )
    cm.init("system " * 100, "hello " * 400)

    t0 = time.perf_counter()
    _ = cm.get_token_report()
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"S3 estimate too slow: {elapsed:.3f}s"
