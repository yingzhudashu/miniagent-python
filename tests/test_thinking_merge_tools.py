"""合并同轮工具行与思考展示的回归测试。"""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock

import pytest

from tests.memory_helpers import make_knowledge_registry, make_memory_runtime

# Check if prompt_toolkit is available (cli extra)
_HAS_PROMPT_TOOLKIT = importlib.util.find_spec("prompt_toolkit") is not None


@pytest.mark.asyncio
async def test_thinking_display_merge_tool_no_second_step_label():
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    label = "[第 1 轮]"
    await td.show(label, streaming=True, header=label)
    await td.show(label + "思考正文", streaming=True, header=label)
    await td.show("🔧 web_search — intent", streaming=False, header=label)

    label_lines = [t for t, k in sink if k == "label"]
    assert len(label_lines) == 1
    assert "[第 1 轮]" in label_lines[0]

    chunks = [t for t, k in sink if k == "chunk"]
    joined = "".join(chunks)
    assert "🔧 web_search" in joined
    assert "思考正文" in joined


@pytest.mark.asyncio
async def test_thinking_display_merge_two_tools_same_round_one_label():
    """同一轮连续两次工具行仍只打一条轮次 label。"""
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    label = "[第 1 轮]"
    await td.show(label, streaming=True, header=label)
    await td.show(label + "正文", streaming=True, header=label)
    await td.show("🔧 tool_a — x", streaming=False, header=label)
    await td.show("🔧 tool_b — y", streaming=False, header=label)

    label_lines = [t for t, k in sink if k == "label"]
    assert len(label_lines) == 1

    chunks = "".join(t for t, k in sink if k == "chunk")
    assert "tool_a" in chunks and "tool_b" in chunks


@pytest.mark.asyncio
async def test_thinking_display_merge_disabled_extra_label(monkeypatch):
    monkeypatch.setattr("miniagent.assistant.engine.thinking.EXECUTION_THINKING_MERGE_TOOLS", False)
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    label = "[第 1 轮]"
    await td.show(label, streaming=True, header=label)
    await td.show(label + "x", streaming=True, header=label)
    await td.show("🔧 t — i", streaming=False, header=label)

    label_lines = [t for t, k in sink if k == "label"]
    assert len(label_lines) == 2


@pytest.mark.asyncio
async def test_engine_history_merges_two_tools_under_turn(monkeypatch):
    from miniagent.agent.monitor import DefaultToolMonitor
    from miniagent.agent.types.agent import AgentRunResult
    from miniagent.assistant.engine.turn_service import AssistantTurnService
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry

    async def fake_run_agent(*args, **kwargs):
        ot = kwargs.get("on_thinking")
        await ot("[第 1 轮]", True, "[第 1 轮]")
        await ot("[第 1 轮]x", True, "[第 1 轮]")
        await ot("🔧 a — 1", False, "[第 1 轮]")
        await ot("🔧 b — 2", False, "[第 1 轮]")
        return AgentRunResult(reply="ok")

    monkeypatch.setattr("miniagent.assistant.engine.turn_service.run_agent", fake_run_agent)

    ctx = type("Ctx", (), {})()
    ctx.conversation_history = []

    class SM:
        def get_or_create(self, sk, opts):
            return ctx

        def get_session_files_path(self, sk: str) -> None:
            return None

        async def save_session_history_async(self, sk: str) -> None:
            pass

    engine = AssistantTurnService()
    engine.thinking.set_output_sink(lambda *_a, **_k: None)

    await engine.run_agent_with_thinking(
        "hi",
        "sess",
        [],
        None,
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        client=MagicMock(),
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        session_manager=SM(),
        is_feishu=False,
    )

    thinking_msgs = [m for m in ctx.conversation_history if m.get("role") == "thinking"]
    assert len(thinking_msgs) == 1
    content = thinking_msgs[0]["content"]
    assert content.count("🔧") == 2
    assert "a — 1" in content and "b — 2" in content


@pytest.mark.asyncio
async def test_engine_history_merges_tool_under_turn(monkeypatch):
    from miniagent.agent.monitor import DefaultToolMonitor
    from miniagent.agent.types.agent import AgentRunResult
    from miniagent.assistant.engine.turn_service import AssistantTurnService
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry

    async def fake_run_agent(*args, **kwargs):
        ot = kwargs.get("on_thinking")
        await ot("[第 1 轮]", True, "[第 1 轮]")
        await ot("[第 1 轮]brain text", True, "[第 1 轮]")
        await ot("🔧 web_search — q", False, "[第 1 轮]")
        return AgentRunResult(reply="reply")

    monkeypatch.setattr("miniagent.assistant.engine.turn_service.run_agent", fake_run_agent)

    ctx = type("Ctx", (), {})()
    ctx.conversation_history = []

    class SM:
        def get_or_create(self, sk, opts):
            return ctx

        def get_session_files_path(self, sk: str) -> None:
            return None

        async def save_session_history_async(self, sk: str) -> None:
            pass

    engine = AssistantTurnService()
    engine.thinking.set_output_sink(lambda *_a, **_k: None)

    await engine.run_agent_with_thinking(
        "hi",
        "sess",
        [],
        None,
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        client=MagicMock(),
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        session_manager=SM(),
        is_feishu=False,
    )

    thinking_msgs = [m for m in ctx.conversation_history if m.get("role") == "thinking"]
    assert len(thinking_msgs) == 1
    content = thinking_msgs[0]["content"]
    assert "🔧 web_search" in content
    assert "brain text" in content
    assert content.index("[第 1 轮]") < content.index("🔧")


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed (cli extra)")
async def test_cli_thinking_rich_sends_ansi_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("miniagent.assistant.engine.thinking._cli_thinking_rich_enabled", lambda: True)
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    calls: list[dict[str, object]] = []

    def sink(text: str, kind: str = "chunk", *, ansi_markdown: str | None = None) -> None:
        calls.append({"text": text, "kind": kind, "ansi_markdown": ansi_markdown})

    td.set_output_sink(sink)
    md = "## Sec\n\n| x | y |\n|---|---|\n| 1 | 2 |\n"
    await td.show(md, streaming=False, header="")
    assert any(c.get("ansi_markdown") for c in calls)


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_PROMPT_TOOLKIT, reason="prompt_toolkit not installed (cli extra)")
async def test_cli_thinking_rich_falls_back_when_no_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("miniagent.assistant.engine.thinking._cli_thinking_rich_enabled", lambda: True)
    monkeypatch.setattr(
        "miniagent.assistant.engine.markdown_cli.render_markdown_to_ansi",
        lambda *_a, **_k: None,
    )
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    records: list[tuple[str | None, str | None]] = []

    def sink(text: str, kind: str = "chunk", *, ansi_markdown: str | None = None) -> None:
        records.append((text, ansi_markdown))

    td.set_output_sink(sink)
    md = "## Sec\n\n| x | y |\n|---|---|\n| 1 | 2 |\n"
    await td.show(md, streaming=False, header="")
    assert all(am is None for _, am in records)
    joined = "".join(t or "" for t, _ in records)
    assert "|" in joined


@pytest.mark.asyncio
async def test_feishu_same_header_after_merge_tools_not_new_round() -> None:
    """同一步内工具后继续流式：飞书不应新开思考卡（is_new_round=False）。"""
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    flags: list[bool] = []

    async def feishu_send(
        chat_id: str,
        text: str,
        template: str,
        *,
        is_new_round: bool = False,
        streaming: bool = True,
        merge_tools: bool = False,
        finalize_only: bool = False,
    ) -> None:
        flags.append(is_new_round)

    td.enable_feishu("sk", "oc_x", feishu_send)
    hdr = "[步骤 1/3] x"
    await td.show(hdr, session_key="sk", streaming=True, header=hdr)
    await td.show(hdr + "a", session_key="sk", streaming=True, header=hdr)
    await td.show("`t` · 成功", session_key="sk", streaming=False, header=hdr)
    await td.show(hdr + "b", session_key="sk", streaming=True, header=hdr)
    assert flags[0] is True
    assert flags[1] is False
    assert flags[2] is False
    assert flags[3] is False


@pytest.mark.asyncio
async def test_cli_same_header_after_merge_tools_one_label_and_no_dup_prefix() -> None:
    """同一步内工具后继续流式：CLI 仅一条步骤标签，且不累打上一子轮正文（对齐 _joined_phase_cumulative）。"""
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    hdr = "[步骤 1/3] x"
    sep = "\n\n"
    await td.show(hdr, streaming=True, header=hdr)
    await td.show("alpha", streaming=True, header=hdr)
    await td.show("`t` · 成功", streaming=False, header=hdr)
    # 模拟执行器第二子轮首包：上一段 + 分隔 + 新正文
    await td.show(f"alpha{sep}beta", streaming=True, header=hdr)

    label_lines = [t for t, k in sink if k == "label"]
    assert len(label_lines) == 1
    assert hdr in label_lines[0]

    chunks = "".join(t for t, k in sink if k == "chunk")
    assert chunks.count("alpha") == 1
    assert "beta" in chunks
    assert "成功" in chunks


@pytest.mark.asyncio
async def test_cli_phase_changed_resets_stream_without_feishu() -> None:
    """纯 CLI（无飞书）：流式 header 切换时收尾并重置，应出现两条步骤标签。"""
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    h_plan = "[评估与计划]"
    h_exec = "[执行]"
    await td.show(h_plan, streaming=True, header=h_plan)
    await td.show("planning body", streaming=True, header=h_plan)
    await td.show("exec body", streaming=True, header=h_exec)

    label_lines = [t for t, k in sink if k == "label"]
    assert len(label_lines) == 2
    assert h_plan in label_lines[0]
    assert h_exec in label_lines[1]

    chunks = "".join(t for t, k in sink if k == "chunk")
    assert "planning body" in chunks
    assert "exec body" in chunks


@pytest.mark.asyncio
async def test_cli_tools_merge_without_prior_streaming() -> None:
    """LLM 无正文仅工具调用时，首个工具行初始化流状态，后续工具行合并。"""
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    label = "[执行]"

    # 无 streaming=True 调用，直接非流式工具行
    await td.show("🔧 tool_a — x", streaming=False, header=label)
    await td.show("🔧 tool_b — y", streaming=False, header=label)

    label_lines = [t for t, k in sink if k == "label"]
    # 应只有一条轮次 label（由首个工具初始化）
    assert len(label_lines) == 1
    assert label in label_lines[0]

    chunks = "".join(t for t, k in sink if k == "chunk")
    assert "tool_a" in chunks and "tool_b" in chunks


@pytest.mark.asyncio
async def test_cli_tools_no_merge_when_disabled_and_no_prior_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("miniagent.assistant.engine.thinking.EXECUTION_THINKING_MERGE_TOOLS", False)
    from miniagent.assistant.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk") -> None:
        sink.append((text, kind))

    td.set_output_sink(capture)
    label = "[执行]"

    await td.show("🔧 tool_a — x", streaming=False, header=label)
    await td.show("🔧 tool_b — y", streaming=False, header=label)

    label_lines = [t for t, k in sink if k == "label"]
    # merge_tools 关闭时，每个工具应有独立 label
    assert len(label_lines) == 2
