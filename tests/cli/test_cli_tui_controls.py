"""全屏 TUI transcript 控件的鼠标和渲染契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.mouse_events import MouseButton, MouseEventType, MouseModifier

from miniagent.ui.tui import controls


def _event(
    kind: MouseEventType,
    *,
    x: int = 0,
    y: int = 0,
    button: MouseButton = MouseButton.LEFT,
    modifiers=frozenset(),
) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=kind,
        position=SimpleNamespace(x=x, y=y),
        button=button,
        modifiers=modifiers,
    )


def _build(monkeypatch):
    invalidations: list[bool] = []
    monkeypatch.setattr(
        controls,
        "get_app",
        lambda: SimpleNamespace(invalidate=lambda: invalidations.append(True)),
    )
    state = {
        "wrap": [False],
        "h": [0],
        "out": [0],
        "selection": [""],
        "start": [None],
        "end": [None],
        "down": [False],
        "copy": [False],
        "transcript": [("class:x", "hello"), ("class:x", "world")],
        "window": [None],
        "pane": SimpleNamespace(vertical_scroll=0),
    }
    calls: list[tuple] = []

    def apply_h(delta):
        calls.append(("h", delta))
        state["h"][0] = max(0, min(20, state["h"][0] + delta))

    def apply_v(delta, source):
        calls.append(("v", delta, source))
        state["out"][0] += delta

    def clear_selection():
        state["start"][0] = state["end"][0] = None
        state["selection"][0] = ""
        state["down"][0] = False

    def extract_selection():
        start, end = sorted((state["start"][0], state["end"][0]))
        return "helloworld"[start:end]

    args = dict(
        flatten_transcript_for_pt=lambda: state["transcript"],
        apply_horizontal_scroll=apply_h,
        apply_transcript_scroll=apply_v,
        copy_mode_active=state["copy"],
        copy_mode_mouse_down=state["down"],
        clear_selection=clear_selection,
        extract_selection_text=extract_selection,
        rendered_position_to_offset=lambda row, column: min(10, row * 5 + column),
        rendered_text_length=lambda: 10,
        max_output_scroll=lambda: 20,
        set_transcript_scroll=lambda position, source: calls.append(("set-v", position, source)),
        scroll_pane=lambda: state["pane"],
        selection_end=state["end"],
        selection_start=state["start"],
        selection_text=state["selection"],
        should_wrap_lines=lambda: state["wrap"][0],
        output_scroll_ref=[None],
        transcript=state["transcript"],
        transcript_window_ref=state["window"],
        viewport_cols=lambda: 20,
        viewport_rows=lambda: 10,
        wheel_line_step=lambda: 3,
        horizontal_scroll=state["h"],
        max_horizontal_scroll=lambda: 20,
        begin_viewport_measure=lambda columns, rows: calls.append(("begin", columns, rows)),
        finish_viewport_measure=lambda height: calls.append(("finish", height)),
    )
    inner, window, pane, hbar = controls.create_transcript_controls(**args)
    return state, calls, invalidations, inner, window, pane, hbar


def test_transcript_mouse_scroll_drag_and_copy(monkeypatch) -> None:
    state, calls, invalidations, _inner, window, _pane, _hbar = _build(monkeypatch)
    control = window.content

    assert control.mouse_handler(_event(MouseEventType.SCROLL_UP)) is None
    assert control.mouse_handler(_event(MouseEventType.SCROLL_DOWN)) is None
    assert calls[:2] == [("v", -3, "mouse.SCROLL_UP"), ("v", 3, "mouse.SCROLL_DOWN")]

    pan = frozenset({MouseModifier.SHIFT})
    assert control.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=4, modifiers=pan)) is None
    assert control.mouse_handler(_event(MouseEventType.MOUSE_MOVE, x=1, modifiers=pan)) is None
    assert control.mouse_handler(_event(MouseEventType.MOUSE_UP, x=1, modifiers=pan)) is None
    assert any(item[0] == "h" for item in calls)

    assert control.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=2, y=0)) is None
    assert control.mouse_handler(_event(MouseEventType.MOUSE_MOVE, x=8, y=0)) is None
    assert control.mouse_handler(_event(MouseEventType.MOUSE_UP, x=8, y=0)) is None
    assert state["down"][0] is False
    assert state["selection"][0] == "lloworl"
    assert invalidations


def test_transcript_control_empty_copy_and_missing_pane(monkeypatch) -> None:
    state, _calls, _invalidations, _inner, window, _pane, _hbar = _build(monkeypatch)
    control = window.content
    state["copy"][0] = True
    state["transcript"].clear()
    assert control.mouse_handler(_event(MouseEventType.MOUSE_DOWN)) is NotImplemented

    state["transcript"].append(("class:x", "x"))
    state["pane"] = None
    assert control.mouse_handler(_event(MouseEventType.MOUSE_DOWN)) is NotImplemented


def test_horizontal_scrollbar_render_and_mouse(monkeypatch) -> None:
    state, calls, _invalidations, _inner, _window, _pane, hbar = _build(monkeypatch)
    hcontrol = hbar.content
    state["wrap"][0] = True
    assert hcontrol.preferred_height(20, 5, False, None) == 0
    assert hcontrol.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=1)) is NotImplemented

    state["wrap"][0] = False
    assert hcontrol.preferred_height(20, 5, False, None) == 1
    rendered = hcontrol.create_content(20, 1).get_line(0)
    assert rendered
    assert hcontrol.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=0)) is None
    assert hcontrol.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=19)) is None
    assert hcontrol.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=10)) is None
    assert hcontrol.mouse_handler(_event(MouseEventType.MOUSE_MOVE, x=10)) is None
    assert any(item[0] == "h" for item in calls)

    state["h"][0] = 0
    state["wrap"][0] = False
    state["transcript"].clear()
    assert hcontrol.mouse_handler(_event(MouseEventType.MOUSE_DOWN, x=3)) is None
