"""TUI 输入区自适应高度与真实视口几何测试。"""

from __future__ import annotations

from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from miniagent.engine.cli_tui import _TuiViewport
from miniagent.engine.cli_tui_app import _create_input_prompt


def test_input_prompt_starts_at_one_row_grows_and_caps_at_six() -> None:
    buffer = Buffer()
    buffer.load_history_if_not_yet_loaded = lambda: None  # type: ignore[method-assign]
    prompt = _create_input_prompt(buffer)

    def preferred(text: str, width: int = 20) -> int:
        buffer.set_document(Document(text, cursor_position=len(text)))
        return prompt.preferred_height(width, 20).preferred

    assert preferred("") == 1
    assert preferred("single line") == 1
    assert preferred("a\nb\nc") == 3
    assert preferred("x" * 50, 20) == 4
    assert preferred("x" * 50, 40) == 2
    assert preferred("\n".join(str(index) for index in range(8))) == 6


def test_viewport_resize_clamps_scroll_and_preserves_reading_progress() -> None:
    size = SimpleNamespace(rows=24, columns=80)
    app = SimpleNamespace(output=SimpleNamespace(get_size=lambda: size))
    lazy_loads: list[bool] = []
    stick_bottom = [True]
    viewport = _TuiViewport(lambda: app, stick_bottom, lambda: lazy_loads.append(True))
    pane = SimpleNamespace(vertical_scroll=0, show_scrollbar=lambda: True)
    viewport.output_scroll_ref[0] = pane

    viewport.begin_measure(79, 10)
    viewport.finish_measure(100)
    assert viewport.rows() == 10
    assert viewport.columns() == 79
    assert pane.vertical_scroll == 90

    stick_bottom[0] = False
    pane.vertical_scroll = 45
    viewport.begin_measure(39, 10)
    viewport.finish_measure(180)
    assert pane.vertical_scroll == 85  # 45/90 of the new 170-row range.

    viewport.begin_measure(39, 20)
    viewport.finish_measure(180)
    assert pane.vertical_scroll == 85
    viewport.finish_measure(30)
    assert pane.vertical_scroll == 10

    viewport.scroll(-20, "test")
    assert pane.vertical_scroll == 0
    assert lazy_loads


def test_viewport_uses_actual_longest_line_for_horizontal_bounds() -> None:
    app = SimpleNamespace(
        output=SimpleNamespace(get_size=lambda: SimpleNamespace(rows=24, columns=30))
    )
    viewport = _TuiViewport(lambda: app, [False], lambda: None)
    pane = SimpleNamespace(vertical_scroll=0, show_scrollbar=lambda: True)
    viewport.output_scroll_ref[0] = pane
    viewport.begin_measure(29, 10)
    viewport.report_content_width(80)

    assert not viewport.should_wrap()
    assert viewport.max_horizontal() == 51
    viewport.apply_horizontal(1000)
    assert viewport.horizontal_scroll[0] == 51

    viewport.begin_measure(60, 10)
    assert viewport.should_wrap()
    assert viewport.horizontal_scroll[0] == 0
