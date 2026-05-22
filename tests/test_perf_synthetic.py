"""合成性能冒烟：Mock LLM + 本地路径（默认 CI 可跑，阈值宽松）。"""

from __future__ import annotations

import tempfile
from types import SimpleNamespace
from typing import Any
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
from tests.perf_helpers import (
    assert_two_medians_within_ratio,
    median_wall_seconds,
    median_wall_seconds_async,
    tracemalloc_peak_diff_mb,
)


def _large_tool_schemas(count: int = 40) -> list[dict[str, Any]]:
    """与 S3/S4 对齐的大型 OpenAI-style tool schema 列表（仅用于 perf 合成）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x" * 200,
                "parameters": {
                    "type": "object",
                    "properties": {f"p{j}": {"type": "string"} for j in range(8)},
                },
            },
        }
        for i in range(count)
    ]


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

    med_a = await median_wall_seconds_async(3, _once)
    med_b = await median_wall_seconds_async(3, _once)
    assert_two_medians_within_ratio(med_a, med_b, msg="S1 run-to-run median jitter")

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

    tools = _large_tool_schemas(40)

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


@pytest.mark.perf
def test_s4_tool_budget_burst_median_under_cap() -> None:
    """S4：多工具下反复取 token 报告（依赖工具 token 缓存，防 needs_compression 路径退化）。"""
    from miniagent.memory.context import DefaultContextManager

    tools = _large_tool_schemas(40)
    cm = DefaultContextManager(
        context_window=128000,
        compress_threshold=0.99,
        tools=tools,
        overflow_strategy="summarize",
    )
    cm.init("system " * 100, "hello " * 400)

    def burst() -> None:
        for _ in range(250):
            _ = cm.get_token_report()

    med = median_wall_seconds(5, burst)
    assert med < 10.0, f"S4 budget burst too slow: {med:.3f}s"


@pytest.mark.perf
def test_s5_normalize_lark_md_median_under_cap() -> None:
    """S5：飞书 lark_md 规范化纯 CPU 路径（不访问网络；与 poll_server 热点对照）。"""
    from miniagent.feishu.poll_server import _normalize_lark_md

    lines: list[str] = []
    for i in range(450):
        lines.extend(["**bold**", "a * b", "---", f"段落{i} 测试"])
    body = "\n\n".join(lines)

    def once() -> None:
        _normalize_lark_md(body)

    med = median_wall_seconds(5, once)
    assert med < 8.0, f"S5 normalize_lark_md too slow: {med:.3f}s"


@pytest.mark.perf
def test_s6_memory_store_batch_tracemalloc_peak_loose() -> None:
    """S6：批量 add_entry + flush 的分配峰值（宽松上界，防灾难性分配回归）。"""
    import asyncio
    from datetime import datetime, timezone

    from miniagent.memory.keyword_index import KeywordIndex
    from miniagent.memory.store import DefaultMemoryStore
    from miniagent.types.memory import MemoryEntryInput, SessionMemory

    def run() -> None:
        async def body() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                ki = KeywordIndex(state_dir=tmp)
                store = DefaultMemoryStore(state_dir=tmp, keyword_index=ki)
                sid = "s6-perf"
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
                for i in range(25):
                    await store.add_entry(
                        sid,
                        MemoryEntryInput(
                            timestamp=now,
                            user_snippet=f"用户片段{i} 关键词测试",
                            summary=f"摘要{i}",
                            facts=[f"事实{i}"],
                        ),
                    )
                store.flush_keyword_index()

        asyncio.run(body())

    peak_mb = tracemalloc_peak_diff_mb(run)
    assert peak_mb < 180.0, f"S6 tracemalloc peak too high: {peak_mb:.1f} MiB"


@pytest.mark.perf
def test_s7_exec_payload_json_serialize_median_under_cap() -> None:
    """S7：messages + tools 的 json.dumps 本地耗时上界（与 execute_plan 组装路径对齐）。"""
    from miniagent.core.request_payload import serialize_exec_payload_sample

    tools = _large_tool_schemas(28)

    def once() -> None:
        ml, tl = serialize_exec_payload_sample(tools, user_turn_pairs=6)
        assert ml > 1000 and tl > 500

    med_a = median_wall_seconds(3, once)
    med_b = median_wall_seconds(3, once)
    assert_two_medians_within_ratio(med_a, med_b, msg="S7 run-to-run median jitter")

    med = median_wall_seconds(5, once)
    assert med < 4.0, f"S7 serialize_exec_payload_sample too slow: {med:.3f}s"
