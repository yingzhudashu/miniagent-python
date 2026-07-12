"""合成性能冒烟：Mock LLM + 本地路径（默认 CI 可跑，阈值宽松）。"""

from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.executor import execute_plan
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.memory.keyword_index import KeywordIndex
from miniagent.memory.store import DefaultMemoryStore
from miniagent.types.config import AgentConfig, SessionBindingConfig
from miniagent.types.memory import MemoryEntryInput, SessionMemory
from miniagent.types.planning import StructuredPlan
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime
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
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_config=SessionBindingConfig(session_registry=sess),
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
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )
        assert "done" in out

    med_a = await median_wall_seconds_async(3, _once)
    med_b = await median_wall_seconds_async(3, _once)
    assert_two_medians_within_ratio(med_a, med_b, msg="S1 run-to-run median jitter")

    med = await median_wall_seconds_async(5, _once)
    assert med < 3.0, f"S1 median wall too high: {med:.3f}s"


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
    assert elapsed < 1.5, f"S3 estimate too slow: {elapsed:.3f}s"


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
    assert peak_mb < 120.0, f"S6 tracemalloc peak too high: {peak_mb:.1f} MiB"


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


@pytest.mark.perf
def test_s8_memory_store_lru_cache_bounded() -> None:
    """S8：连续加载多个会话到 DefaultMemoryStore，验证 LRU cache 大小不超过上限。"""
    from datetime import datetime, timezone

    with tempfile.TemporaryDirectory() as tmp:
        ki = KeywordIndex(state_dir=tmp)
        store = DefaultMemoryStore(state_dir=tmp, keyword_index=ki)

        num_sessions = store._cache_max + 10  # 明确超过当前 JSON 配置的 LRU 上限
        now = datetime.now(timezone.utc).isoformat()

        async def load_many() -> None:
            for i in range(num_sessions):
                sid = f"s8-session-{i}"
                mem = SessionMemory(
                    session_id=sid,
                    cumulative_summary=f"summary {i}",
                    key_facts=[f"fact {i}"],
                    entries=[],
                    total_turns=0,
                    first_seen=now,
                    last_active=now,
                )
                await store.save(mem)

        asyncio.run(load_many())

        # LRU 驱逐后 cache 大小不应超过当前配置的 cache_max。
        assert len(store._cache) <= store._cache_max, (
            f"LRU cache exceeded: {len(store._cache)} > {store._cache_max}"
        )


@pytest.mark.perf
def test_s9_embedding_index_bounded() -> None:
    """S9：EmbeddingIndex 连续添加条目，验证峰值不超过 max_entries。"""
    from miniagent.memory.embedding_search import EmbeddingIndex

    with tempfile.TemporaryDirectory() as tmp:
        idx = EmbeddingIndex(state_dir=tmp)
        idx._max_entries = 200  # 降低上限以加速测试

        for i in range(250):
            entry = MemoryEntryInput(
                timestamp=f"2026-05-22T{i:02d}:00:00Z",
                user_snippet=f"用户片段{i}",
                summary=f"摘要{i}",
                facts=[f"事实{i}"],
            )
            # 模拟 1536 维向量
            idx.index_entry(f"sess-{i}", entry, embedding=[0.1] * 1536)

        assert len(idx._entries) <= idx._max_entries, (
            f"EmbeddingIndex exceeded max_entries: {len(idx._entries)} > {idx._max_entries}"
        )


@pytest.mark.perf
def test_s10_keyword_index_bounded() -> None:
    """S10：KeywordIndex 连续添加条目，验证关键词数不超过上限。"""
    with tempfile.TemporaryDirectory() as tmp:
        ki = KeywordIndex(state_dir=tmp)
        ki._max_entries = 50  # 降低上限以触发驱逐

        for i in range(200):
            entry = MemoryEntryInput(
                timestamp=f"2026-05-22T{i:02d}:00:00Z",
                user_snippet=f"用户片段{i} 关键词{i} 测试{i}",
                summary=f"摘要{i} 描述{i}",
                facts=[f"事实{i}"],
            )
            ki.index_entry(f"sess-{i}", entry)

        assert len(ki._index) <= ki._max_entries, (
            f"KeywordIndex exceeded max_entries: {len(ki._index)} > {ki._max_entries}"
        )


@pytest.mark.perf
def test_s11_memory_store_add_entry_batch_median_under_cap() -> None:
    """S11：同会话连续 add_entry 的锁内加载与缓存路径不应退化。"""
    from datetime import datetime, timezone

    def once() -> None:
        async def body() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                ki = KeywordIndex(state_dir=tmp)
                store = DefaultMemoryStore(state_dir=tmp, keyword_index=ki)
                sid = "s11-perf"
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
                for i in range(20):
                    await store.add_entry(
                        sid,
                        MemoryEntryInput(
                            timestamp=now,
                            user_snippet=f"用户片段{i} 性能 优化",
                            summary=f"摘要{i}",
                            facts=[f"事实{i}"],
                        ),
                    )

        asyncio.run(body())

    med = median_wall_seconds(3, once)
    assert med < 5.0, f"S11 add_entry batch too slow: {med:.3f}s"


@pytest.mark.perf
def test_s12_keyword_index_search_multi_hit_median_under_cap() -> None:
    """S12：多关键词命中同一条目时 registry lookup 复用，搜索成本受控。"""
    from datetime import datetime, timezone

    def once() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            idx = KeywordIndex(state_dir=tmp)
            now = datetime.now(timezone.utc).isoformat()
            for i in range(120):
                entry = MemoryEntryInput(
                    timestamp=now,
                    user_snippet=f"Python 性能 优化 记忆 索引 {i}",
                    summary=f"Python 性能 优化 摘要 {i}",
                    facts=[f"关键词 检索 {i}"],
                )
                idx.index_entry(f"sess-{i}", entry)
            results = idx.search_relevant("Python 性能 优化 记忆 索引", limit=10)
            assert results

    med = median_wall_seconds(5, once)
    assert med < 3.0, f"S12 keyword search too slow: {med:.3f}s"


@pytest.mark.perf
def test_s13_feishu_thinking_card_cache_median_under_cap() -> None:
    """S13：重复 thinking card 渲染应复用 normalized body/card JSON。"""
    from miniagent.engine.thinking import ThinkingDisplay
    from miniagent.feishu.poll_server import _thinking_card_json_cached

    body = "\n\n".join(["### 标题", "**bold**", "a * b", "| A | B |", "|---|---|", "| 1 | 2 |"] * 80)

    def once() -> None:
        td = ThinkingDisplay()
        st = td.thinking_state("s13")
        first = _thinking_card_json_cached(st, body, "gray", "s13")
        for _ in range(80):
            assert _thinking_card_json_cached(st, body, "gray", "s13") == first

    med = median_wall_seconds(5, once)
    assert med < 2.0, f"S13 thinking card cache too slow: {med:.3f}s"
