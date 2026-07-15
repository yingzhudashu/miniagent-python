"""TUI 输出绑定的 Markdown、思考流、出站适配和清理测试。"""

from __future__ import annotations

from types import SimpleNamespace

from miniagent.assistant.engine import cli_tui_output as output_module
from miniagent.ui.cli.state import TuiViewState


class _Thinking:
    def __init__(self) -> None:
        self.sinks = []
        self.width = None

    def set_output_sink(self, sink) -> None:
        self.sinks.append(sink)

    def set_cli_markdown_width(self, width) -> None:
        self.width = width


class _Coordinator:
    def __init__(self, append, append_ansi, *, on_turn_end) -> None:
        self.append = append
        self.append_ansi = append_ansi
        self.on_turn_end = on_turn_end
        self.live = True

    def is_live(self, _key):
        return self.live

    def defer(self, _key, callback):
        callback()

    def make_session_append(self, _key):
        return self.append

    def make_session_append_ansi(self, _key):
        return self.append_ansi


class _Adapter:
    def __init__(self, final, error, status, thinking) -> None:
        self.final = final
        self.error = error
        self.status = status
        self.thinking = thinking


def test_output_bindings_cover_markdown_stream_and_delivery(monkeypatch) -> None:
    appended = []
    transcript = []
    trimmed = []
    snapped = []
    streams = {}
    registered = []
    thinking = _Thinking()
    engine = SimpleNamespace(thinking=thinking)
    ctx = SimpleNamespace(
        cli_transcript_coordinator=None,
        create_feishu_handler_factory=None,
        register_shutdown_tracked_task=lambda task: registered.append(task),
    )
    state = {}

    def append(style, text):
        appended.append((style, text))

    def stream_state(key):
        return streams.setdefault(key, SimpleNamespace(active=False, text="", start_idx=-1))

    adapter_holder = []
    channels = SimpleNamespace(register=lambda adapter, **_kwargs: adapter_holder.append(adapter))
    dispatcher = SimpleNamespace(publish=lambda event: ("task", event))
    monkeypatch.setattr(output_module, "get_config", lambda *_args: True)
    monkeypatch.setattr(output_module, "get_app", lambda: SimpleNamespace(invalidate=lambda: None))
    import miniagent.assistant.engine.markdown_cli as markdown_module

    monkeypatch.setattr(markdown_module, "render_markdown_to_ansi", lambda text, **_kw: f"ANSI:{text}")

    bindings = output_module.create_tui_output_bindings(
        runtime_context=ctx,
        state=state,
        engine=engine,
        outbound_channels=channels,
        cli_channel_adapter=_Adapter,
        transcript_coordinator_class=_Coordinator,
        cli_outbound_dispatcher=dispatcher,
        build_cli_thinking_event=lambda *args, **kwargs: (args, kwargs),
        streaming_think_by_session=streams,
        stream_state=stream_state,
        transcript=transcript,
        stick_bottom=[True],
        safe_ansi=lambda text: [("class:ansi", text)],
        trim_transcript=lambda: trimmed.append(True),
        append_transcript=append,
        append_ansi_transcript=lambda text: appended.append(("ansi", text)),
        markdown_render_width=lambda: 80,
        output_at_bottom=lambda: True,
        snap_output_bottom=lambda: snapped.append(True),
        viewport_cols=lambda: 100,
        rule_line_width_for_vp=lambda cols: cols - 2,
    )

    bindings.term_write("**hello**", "ansigreen")
    assert transcript and trimmed
    bindings.cli_rule_heavy()
    bindings.cli_rule_light()
    assert any("═" in text for _, text in appended)
    assert bindings.rule_line_width() == 98

    inner_sink = thinking.sinks[0]
    inner_sink("Thinking", "label", session_key="s")
    inner_sink("first", "chunk", session_key="s")
    inner_sink(" second", "chunk", session_key="s")
    inner_sink("", "chunk", session_key="s", ansi_markdown="rendered\n")
    assert streams["s"].text == "first second"
    assert snapped

    adapter = adapter_holder[0]
    adapter.error("s", "error")
    adapter.status("s", "status")
    event = SimpleNamespace(
        content="think",
        metadata={"fragment_kind": "chunk", "ansi_markdown": "ansi"},
        target=SimpleNamespace(conversation_id="s"),
    )
    adapter.thinking(event)
    thinking.sinks[-1]("published", session_key="s")
    assert registered

    assert "cli_render_width" in state and "cli_markdown_width" in state
    bindings.clear_cli_format_widths()
    assert "cli_render_width" not in state and "cli_markdown_width" not in state


def test_default_tui_view_shows_evaluation_plan_and_execution_details(monkeypatch) -> None:
    transcript = []
    streams = {}
    view = TuiViewState()
    thinking = _Thinking()
    ctx = SimpleNamespace(
        cli_transcript_coordinator=None,
        create_feishu_handler_factory=None,
        register_shutdown_tracked_task=lambda _task: None,
    )

    def stream_state(key):
        return streams.setdefault(key, SimpleNamespace(active=False, text="", start_idx=-1))

    monkeypatch.setattr(output_module, "get_config", lambda *_args: True)
    monkeypatch.setattr(output_module, "get_app", lambda: SimpleNamespace(invalidate=lambda: None))
    monkeypatch.setattr(
        "miniagent.assistant.engine.markdown_cli.render_markdown_to_ansi",
        lambda text, **_kwargs: text,
    )
    output_module.create_tui_output_bindings(
        runtime_context=ctx,
        state={},
        engine=SimpleNamespace(thinking=thinking),
        outbound_channels=SimpleNamespace(register=lambda *_args, **_kwargs: None),
        cli_channel_adapter=_Adapter,
        transcript_coordinator_class=_Coordinator,
        cli_outbound_dispatcher=SimpleNamespace(publish=lambda _event: None),
        build_cli_thinking_event=lambda *args, **kwargs: (args, kwargs),
        streaming_think_by_session=streams,
        stream_state=stream_state,
        transcript=transcript,
        stick_bottom=[True],
        safe_ansi=lambda text: [("class:ansi", text)],
        trim_transcript=lambda: None,
        append_transcript=lambda style, text: transcript.append((style, text)),
        append_ansi_transcript=lambda text: transcript.append(("ansi", text)),
        markdown_render_width=lambda: 80,
        output_at_bottom=lambda: True,
        snap_output_bottom=lambda: None,
        viewport_cols=lambda: 100,
        rule_line_width_for_vp=lambda cols: cols - 2,
        reasoning_expanded=lambda: view.reasoning_expanded,
    )

    sink = thinking.sinks[0]
    sink("评估与计划\n", "label", session_key="s")
    sink("先分析需求，再制定步骤。", session_key="s")
    sink("执行\n", "label", session_key="s")
    sink("正在调用工具并整理答案。", session_key="s")

    rendered = "".join(str(item[1]) for item in transcript)
    assert "评估与计划" in rendered
    assert "先分析需求，再制定步骤。" in rendered
    assert "执行" in rendered
    assert "正在调用工具并整理答案。" in rendered
