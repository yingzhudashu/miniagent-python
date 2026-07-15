"""TUI 视口和输入分派对象的行为矩阵。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import cli_tui, cli_tui_keybindings, cli_tui_output


def _app(*, rows: int = 24, columns: int = 80):
    size = SimpleNamespace(rows=rows, columns=columns)
    return SimpleNamespace(output=SimpleNamespace(get_size=lambda: size))


def test_viewport_scroll_width_and_fallbacks() -> None:
    lazy = MagicMock()
    viewport = cli_tui._TuiViewport(lambda: _app(rows=30, columns=100), [True], lazy)
    assert viewport.rows() == 26
    assert viewport.columns() == 99
    assert viewport.at_bottom() is True
    assert viewport.content_height() == 0

    window = SimpleNamespace(horizontal_scroll=-1)
    pane = SimpleNamespace(
        vertical_scroll=10,
        max_available_height=100,
        show_scrollbar=lambda: True,
        content=SimpleNamespace(
            preferred_height=lambda *_args: SimpleNamespace(preferred=50)
        ),
    )
    viewport.output_scroll_ref[0] = pane
    viewport.transcript_window_ref[0] = window
    viewport.stick_bottom[0] = False
    viewport.begin_measure(99, 26)
    viewport.finish_measure(50)
    assert viewport.columns() == 99
    assert viewport.content_height() == 50
    assert viewport.max_output() == 24
    assert viewport.at_bottom() is False
    assert viewport.wheel_step() == 4
    assert viewport.max_horizontal() == 0
    viewport.begin_measure(29, 26)
    viewport.finish_measure(50)
    viewport.report_content_width(80)
    viewport.apply_horizontal(15)
    assert window.horizontal_scroll == 15
    viewport.reset_horizontal()
    assert window.horizontal_scroll == 0
    viewport.snap_bottom()
    assert pane.vertical_scroll == 24
    viewport.scroll(-30, "test")
    assert pane.vertical_scroll == 0
    lazy.assert_called_once()
    viewport.scroll(8, "test")
    assert pane.vertical_scroll == 8
    assert viewport.is_scrollbar_click(SimpleNamespace(position=SimpleNamespace(x=28))) is True

    broken = cli_tui._TuiViewport(
        lambda: (_ for _ in ()).throw(RuntimeError("tty")), [True], lazy
    )
    assert broken.rows() == 20
    assert broken.columns() == 79


def _runtime() -> dict:
    state = {"active_session_id": "s1"}
    engine = SimpleNamespace(
        set_active_session_key=MagicMock(),
        get_confirmation_channel=MagicMock(return_value=None),
    )
    return {
        "ctx": SimpleNamespace(cli_transcript_append=object()),
        "state": state,
        "engine": engine,
        "registry": object(),
        "monitor": object(),
        "channel_router": object(),
        "outbound_channels": SimpleNamespace(send=AsyncMock()),
        "inbound_turns": SimpleNamespace(submit=AsyncMock()),
        "process_input": AsyncMock(),
        "build_cli_inbound_message": lambda text, session, **_kwargs: (text, session),
        "build_cli_outbound_event": lambda *args, **kwargs: (args, kwargs),
        "outbound_event_kind": SimpleNamespace(STATUS="status"),
        "skill_tb": lambda: [],
        "skill_toolboxes": [],
        "skill_prompts": [],
        "transcript_plain": lambda: "history",
        "term_write": MagicMock(),
        "reset_transcript": MagicMock(),
        "input_buffer": object(),
        "history_file": "history.txt",
        "clear_widths": MagicMock(),
    }


@pytest.mark.asyncio
async def test_handle_tui_copy_stop_and_command(monkeypatch) -> None:
    runtime = _runtime()
    monkeypatch.setattr(cli_tui, "copy_text_to_system_clipboard", lambda _text: True)
    assert await cli_tui._handle_tui_input("/copy", runtime) is False
    assert "已复制" in runtime["term_write"].call_args.args[0]

    shutdown = AsyncMock()
    monkeypatch.setattr(cli_tui, "shutdown_runtime", shutdown)
    assert await cli_tui._handle_tui_input("/stop", runtime) is True
    shutdown.assert_awaited_once()

    import miniagent.assistant.engine.command_dispatch as dispatch_module
    import miniagent.assistant.engine.parallel_config as parallel_module

    monkeypatch.setattr(dispatch_module, "dispatch_command", AsyncMock(return_value="status"))
    monkeypatch.setattr(parallel_module, "resolve_active_session_key", lambda *_args: "s1")
    assert await cli_tui._dispatch_tui_command("/status", runtime) is False
    runtime["outbound_channels"].send.assert_awaited_once()

    monkeypatch.setattr(dispatch_module, "dispatch_command", AsyncMock(return_value="__EXIT__"))
    assert await cli_tui._dispatch_tui_command("/exit", runtime) is True


@pytest.mark.asyncio
async def test_submit_tui_clarification_and_agent(monkeypatch) -> None:
    runtime = _runtime()
    import miniagent.assistant.engine.parallel_config as parallel_module
    from miniagent.agent.types.confirmation import ConfirmationStage

    monkeypatch.setattr(parallel_module, "resolve_active_session_key", lambda *_args: "s1")
    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.CLARIFICATION),
        respond=MagicMock(),
    )
    runtime["engine"].get_confirmation_channel.return_value = channel
    assert await cli_tui._submit_tui_agent_input("answer", runtime) is False
    channel.respond.assert_called_once()
    runtime["inbound_turns"].submit.assert_not_awaited()

    runtime["engine"].get_confirmation_channel.return_value = None
    monkeypatch.setattr(cli_tui, "heartbeat", lambda: None)
    await cli_tui._submit_tui_agent_input("question", runtime)
    runtime["inbound_turns"].submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_interaction_skips_empty_and_falls_back(monkeypatch) -> None:
    runtime = _runtime()
    values = iter([None, "  ", "exit"])
    runtime["app"] = SimpleNamespace(run_async=AsyncMock(side_effect=lambda: next(values)))
    assert await cli_tui._run_tui_interaction(**runtime) is False

    runtime = _runtime()
    runtime["app"] = SimpleNamespace(run_async=AsyncMock(side_effect=RuntimeError("screen")))
    fallback = AsyncMock()
    monkeypatch.setattr(cli_tui, "run_cli_loop_fallback", fallback)
    assert await cli_tui._run_tui_interaction(**runtime) is True
    fallback.assert_awaited_once()
    runtime["clear_widths"].assert_called_once()


def _output_bindings():
    streams = {}
    transcript = []
    appended = []
    coordinator = SimpleNamespace(
        is_live=MagicMock(return_value=True),
        defer=MagicMock(),
        make_session_append=lambda _key: MagicMock(),
        make_session_append_ansi=lambda _key: MagicMock(),
    )
    binding = object.__new__(cli_tui_output._TuiOutputBindings)
    binding.coordinator = coordinator
    binding.streaming_think_by_session = streams
    binding.transcript = transcript
    binding.stick_bottom = [False]
    binding.safe_ansi = lambda value: [("class:ansi", value)]
    binding.trim_transcript = MagicMock()
    binding.append_transcript = lambda style, text: appended.append((style, text))
    binding.append_ansi_transcript = MagicMock()
    binding.markdown_render_width = lambda: 80
    binding.output_at_bottom = lambda: False
    binding.snap_output_bottom = MagicMock()
    binding.viewport_cols = lambda: 100
    binding.rule_line_width_for_vp = lambda value: value - 2
    binding.state = {"cli_render_width": 1, "cli_markdown_width": 1}
    binding.ctx = SimpleNamespace(register_shutdown_tracked_task=MagicMock())
    binding.cli_outbound_dispatcher = SimpleNamespace(publish=MagicMock(return_value="task"))
    binding.build_cli_thinking_event = MagicMock(return_value="event")

    def stream_state(key):
        return streams.setdefault(key, SimpleNamespace(active=False, text="", start_idx=-1))

    binding.stream_state = stream_state
    return binding, transcript, appended, streams


def test_output_binding_stream_fallback_and_delivery(monkeypatch) -> None:
    binding, transcript, appended, streams = _output_bindings()
    import miniagent.assistant.engine.markdown_cli as markdown_module

    monkeypatch.setattr(markdown_module, "render_markdown_to_ansi", lambda text, **_kw: text)
    binding.term_write("")
    binding.term_write("hello", "invalid")
    assert transcript

    binding.thinking_sink_inner("label", "label", session_key="s")
    binding.thinking_sink_inner("one", session_key="s")
    binding.thinking_sink_inner(" two", session_key="s")
    assert streams["s"].text == "one two"
    binding.thinking_sink_inner("", session_key="s", ansi_markdown="ansi\n")
    assert binding.stick_bottom[0] is False

    binding.coordinator.is_live.return_value = False
    binding.thinking_sink("deferred", session_key="s")
    binding.coordinator.defer.assert_called_once()
    binding.clear_cli_format_widths()
    assert binding.state == {}
    binding.cli_rule_heavy()
    binding.cli_rule_light()
    binding.deliver_cli_error("s", "error")
    binding.deliver_cli_status("s", "status")
    binding.publish_cli_thinking("x", session_key="s")
    binding.ctx.register_shutdown_tracked_task.assert_called_with("task")
    assert appended

    monkeypatch.setattr(
        markdown_module,
        "render_markdown_to_ansi",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("render")),
    )
    binding.thinking_sink_inner("plain", session_key="broken")
    assert streams["broken"].active is False


def _key_installer():
    installer = object.__new__(cli_tui_keybindings._TuiKeyBindingInstaller)
    installer.copy_mode_active = [False]
    installer.selection_text = [""]
    installer.selection_start = [None]
    installer.selection_end = [None]
    installer.transcript = [("", "abc")]
    installer.stick_bottom = [False]
    installer.append_transcript = MagicMock()
    installer.toggle_copy_mode = lambda: installer.copy_mode_active.__setitem__(0, not installer.copy_mode_active[0])
    installer.clear_selection = MagicMock()
    installer.get_transcript_char_count = lambda _index: 3
    installer.rendered_text_length = lambda: 3
    installer.has_selection = lambda: bool(installer.selection_text[0])
    installer.extract_selection_text = lambda: "abc"
    installer.viewport_rows = lambda: 20
    installer.apply_transcript_scroll = MagicMock()
    installer.should_wrap_lines = lambda: False
    installer.apply_horizontal_scroll = MagicMock()
    installer.wheel_line_step = lambda: 3
    installer.reset_and_reload_transcript = MagicMock()
    installer.term_write = MagicMock()
    installer.ctx = SimpleNamespace(background_tasks=None)
    history = SimpleNamespace(get_strings=lambda: ["old"])
    installer.input_buffer = SimpleNamespace(
        text="",
        cursor_position=0,
        complete_state=None,
        history=history,
        _working_lines=[""],
        load_history_if_not_yet_loaded=MagicMock(),
        history_backward=MagicMock(),
        history_forward=MagicMock(),
        complete_previous=MagicMock(),
        complete_next=MagicMock(),
        reset=MagicMock(),
    )
    return installer


def test_keybinding_copy_scroll_and_history(monkeypatch) -> None:
    installer = _key_installer()
    app = SimpleNamespace(invalidate=MagicMock(), exit=MagicMock())
    event = SimpleNamespace(app=app)
    installer.on_ctrl_m(event)
    assert installer.copy_mode_active[0] is True
    installer.selection_text[0] = "abc"
    monkeypatch.setattr(cli_tui_keybindings, "copy_text_to_system_clipboard", lambda _text: True)
    installer.on_copy_ctrl_c(event)
    installer.on_copy_enter(event)
    installer.on_copy_escape(event)
    installer.on_copy_select_all(event)
    installer.on_pageup(event)
    installer.on_pagedown(event)
    installer.on_shift_left(event)
    installer.on_shift_right(event)
    installer.on_ctrl_home(event)
    installer.input_buffer.text = "hello"
    installer.on_ctrl_end(event)
    installer.on_scroll_up(event)
    installer.on_scroll_down(event)
    installer.on_up(event)
    installer.on_down(event)
    assert installer.apply_transcript_scroll.call_count == 4
    assert installer.input_buffer.history_backward.called
    assert installer.input_buffer.history_forward.called

    installer.input_buffer.text = "!echo ok"
    monkeypatch.setattr(cli_tui_keybindings, "run_cli_shell_command", lambda _cmd: (True, "ok"))
    installer.on_enter(event)
    installer.input_buffer.text = "question"
    installer.on_enter(event)
    app.exit.assert_called_with(result="question")
    installer.on_exit(event)
    installer.on_clear(event)
