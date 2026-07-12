"""全屏 TUI 键绑定注册与关键行为测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent.engine.cli_tui_keybindings import install_tui_key_bindings


class _KeyBindings:
    def __init__(self) -> None:
        self.handlers: dict[object, list] = {}

    def add(self, key, **_kwargs):
        def decorator(handler):
            self.handlers.setdefault(key, []).append(handler)
            return handler

        return decorator


class _Buffer:
    def __init__(self) -> None:
        self.text = ""
        self.cursor_position = 0
        self.complete_state = None
        self._working_lines = [""]
        self.history = SimpleNamespace(get_strings=lambda: ["old"])
        self.calls: list[str] = []

    def start_completion(self):
        self.calls.append("start_completion")

    def complete_previous(self):
        self.calls.append("complete_previous")

    def complete_next(self):
        self.calls.append("complete_next")

    def reset(self, **_kwargs):
        self.calls.append("reset")

    def load_history_if_not_yet_loaded(self):
        self.calls.append("load_history")

    def history_backward(self):
        self.calls.append("history_backward")

    def history_forward(self):
        self.calls.append("history_forward")


class _App:
    def __init__(self, buffer: _Buffer) -> None:
        self.current_buffer = buffer
        self.results: list[str] = []
        self.invalidations = 0

    def exit(self, *, result: str) -> None:
        self.results.append(result)

    def invalidate(self) -> None:
        self.invalidations += 1


def test_key_bindings_register_and_execute_major_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    kb = _KeyBindings()
    buffer = _Buffer()
    app = _App(buffer)
    event = SimpleNamespace(app=app)
    appended: list[tuple[str, str]] = []
    scrolls: list[tuple[int, str]] = []
    horizontal: list[int] = []
    copy_mode = [False]
    selection_text = [""]
    selection_start = [None]
    selection_end = [None]
    clears: list[bool] = []
    resets: list[bool] = []
    writes: list[tuple[str, str]] = []

    def toggle() -> None:
        copy_mode[0] = not copy_mode[0]

    monkeypatch.setattr(
        "miniagent.engine.cli_tui_keybindings.copy_text_to_system_clipboard",
        lambda _text: True,
    )
    monkeypatch.setattr(
        "miniagent.engine.cli_tui_keybindings.run_cli_shell_command",
        lambda command: (True, f"ran:{command}"),
    )
    monkeypatch.setattr(
        "miniagent.engine.cli_tui_keybindings.sync_preload_buffer_working_lines",
        lambda _buffer: _buffer.calls.append("sync_history"),
    )
    monkeypatch.setattr("miniagent.engine.btw_cmd.cmd_btw_status", lambda _manager: "idle")

    install_tui_key_bindings(
        kb=kb,
        has_focus=lambda _buffer: True,
        condition=lambda callback: callback,
        keys=SimpleNamespace(ScrollUp="scroll-up", ScrollDown="scroll-down"),
        input_buffer=buffer,
        toggle_copy_mode=toggle,
        copy_mode_active=copy_mode,
        append_transcript=lambda style, text: appended.append((style, text)),
        stick_bottom=[False],
        clear_selection=lambda: clears.append(True),
        selection_text=selection_text,
        selection_start=selection_start,
        selection_end=selection_end,
        transcript=[("", "abc")],
        get_transcript_char_count=lambda _index: 3,
        extract_selection_text=lambda: "abc",
        reset_and_reload_transcript=lambda **_kwargs: resets.append(True),
        runtime_context=SimpleNamespace(background_tasks=object()),
        term_write=lambda text, color: writes.append((text, color)),
        viewport_rows=lambda: 10,
        apply_transcript_scroll=lambda amount, source: scrolls.append((amount, source)),
        should_wrap_lines=lambda: False,
        apply_horizontal_scroll=horizontal.append,
        horizontal_scroll=[0],
        max_horizontal_scroll=lambda: 20,
        wheel_line_step=lambda: 3,
    )

    kb.handlers["tab"][0](event)
    kb.handlers["s-tab"][0](event)
    kb.handlers["c-m"][0](event)
    selection_text[0] = "abc"
    kb.handlers["c-c"][0](event)
    kb.handlers["a"][0](event)
    kb.handlers["enter"][0](event)
    selection_start[0] = (0, 0)
    kb.handlers["escape"][0](event)

    buffer.text = "!echo ok"
    kb.handlers["enter"][1](event)
    buffer.text = "hello"
    kb.handlers["enter"][1](event)
    kb.handlers["c-c"][1](event)
    kb.handlers["c-d"][0](event)
    kb.handlers["c-l"][0](event)
    kb.handlers["c-t"][0](event)
    for key in ("pageup", "pagedown", "s-left", "s-right", "c-home", "c-end", "scroll-up", "scroll-down"):
        kb.handlers[key][0](event)
    kb.handlers["up"][0](event)
    kb.handlers["down"][0](event)

    assert {"start_completion", "complete_previous", "history_backward", "history_forward"} <= set(
        buffer.calls
    )
    assert "hello" in app.results
    assert app.results[-2:] == ["__exit__", "__exit__"]
    assert resets and writes and appended
    assert scrolls == [(-5, "pageup"), (5, "pagedown"), (-3, "keys.ScrollUp"), (3, "keys.ScrollDown")]
    assert horizontal == [-10, 10]
