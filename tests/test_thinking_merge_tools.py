"""合并同轮工具行与思考展示的回归测试。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_thinking_display_merge_tool_no_second_step_label():
    from miniagent.engine.thinking import ThinkingDisplay

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
    from miniagent.engine.thinking import ThinkingDisplay

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
    monkeypatch.setenv("MINIAGENT_THINKING_MERGE_TOOLS", "0")
    from miniagent.engine.thinking import ThinkingDisplay

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
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry

    async def fake_run_agent(*args, **kwargs):
        ot = kwargs.get("on_thinking")
        await ot("[第 1 轮]", True, "[第 1 轮]")
        await ot("[第 1 轮]x", True, "[第 1 轮]")
        await ot("🔧 a — 1", False, "[第 1 轮]")
        await ot("🔧 b — 2", False, "[第 1 轮]")
        return "ok"

    monkeypatch.setattr("miniagent.engine.engine.run_agent", fake_run_agent)

    ctx = type("Ctx", (), {})()
    ctx.conversation_history = []

    class SM:
        def get_or_create(self, sk, opts):
            return ctx

        def save_session_history(self, sk: str) -> None:
            pass

    engine = UnifiedEngine()
    engine.thinking.set_output_sink(lambda *_a, **_k: None)

    await engine.run_agent_with_thinking(
        "hi",
        "sess",
        [],
        None,
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
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry

    async def fake_run_agent(*args, **kwargs):
        ot = kwargs.get("on_thinking")
        await ot("[第 1 轮]", True, "[第 1 轮]")
        await ot("[第 1 轮]brain text", True, "[第 1 轮]")
        await ot("🔧 web_search — q", False, "[第 1 轮]")
        return "reply"

    monkeypatch.setattr("miniagent.engine.engine.run_agent", fake_run_agent)

    ctx = type("Ctx", (), {})()
    ctx.conversation_history = []

    class SM:
        def get_or_create(self, sk, opts):
            return ctx

        def save_session_history(self, sk: str) -> None:
            pass

    engine = UnifiedEngine()
    engine.thinking.set_output_sink(lambda *_a, **_k: None)

    await engine.run_agent_with_thinking(
        "hi",
        "sess",
        [],
        None,
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
async def test_cli_thinking_rich_sends_ansi_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CLI_THINKING_RICH", "1")
    from miniagent.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    calls: list[dict[str, object]] = []

    def sink(
        text: str, kind: str = "chunk", *, ansi_markdown: str | None = None
    ) -> None:
        calls.append({"text": text, "kind": kind, "ansi_markdown": ansi_markdown})

    td.set_output_sink(sink)
    md = "## Sec\n\n| x | y |\n|---|---|\n| 1 | 2 |\n"
    await td.show(md, streaming=False, header="")
    assert any(c.get("ansi_markdown") for c in calls)


@pytest.mark.asyncio
async def test_cli_thinking_rich_falls_back_when_no_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CLI_THINKING_RICH", "1")
    monkeypatch.setattr(
        "miniagent.engine.markdown_cli.render_markdown_to_ansi",
        lambda *_a, **_k: None,
    )
    from miniagent.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    records: list[tuple[str | None, str | None]] = []

    def sink(
        text: str, kind: str = "chunk", *, ansi_markdown: str | None = None
    ) -> None:
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
    from miniagent.engine.thinking import ThinkingDisplay

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
