"""全屏 TUI transcript 控件、鼠标选择与滚动条组合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.dimension import LayoutDimension as D
from prompt_toolkit.layout.scrollable_pane import ScrollablePane
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_bindings import NotImplementedOrNone


@dataclass
class _ControlContext:
    """保存 transcript 控件共享的回调与可变引用。"""

    values: dict[str, Any]
    drag_start_x: int | None = None
    dragging_scrollbar: bool = False
    drag_start_y: int = 0

    def __getattr__(self, name: str) -> Any:
        return self.values[name]


class _TranscriptPaneControl(UIControl):
    """将内层文本控件鼠标事件映射到共享 transcript 状态。"""

    def __init__(self, inner: FormattedTextControl, context: _ControlContext) -> None:
        self._inner = inner
        self._context = context

    def preferred_width(self, max_available_width: int) -> int | None:
        return self._inner.preferred_width(max_available_width)

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        return self._inner.preferred_height(
            width, max_available_height, wrap_lines, get_line_prefix
        )

    def create_content(self, width: int, height: int) -> UIContent:
        return self._inner.create_content(width, height)

    def mouse_handler(self, event: MouseEvent) -> NotImplementedOrNone:
        """分派复制、滚轮、滚动条拖动与水平拖动事件。"""
        context = self._context
        if context.copy_mode_active[0]:
            return self._handle_copy(event)
        if event.event_type == MouseEventType.SCROLL_UP:
            context.apply_transcript_scroll(-context.wheel_line_step(), "mouse.SCROLL_UP")
            get_app().invalidate()
            return None
        if event.event_type == MouseEventType.SCROLL_DOWN:
            context.apply_transcript_scroll(context.wheel_line_step(), "mouse.SCROLL_DOWN")
            get_app().invalidate()
            return None
        handled = self._handle_vertical_drag(event)
        if handled:
            return None
        handled = self._handle_horizontal_drag(event)
        if handled:
            return None
        if context.is_scrollbar_click(event) and event.event_type == MouseEventType.MOUSE_DOWN:
            self._start_vertical_drag(event)
            return None
        if not context.should_wrap_lines() and event.event_type == MouseEventType.MOUSE_DOWN:
            context.drag_start_x = getattr(event.position, "x", 0)
            return None
        return self._inner.mouse_handler(event)

    def _handle_vertical_drag(self, event: MouseEvent) -> bool:
        context = self._context
        if not context.dragging_scrollbar:
            return False
        pane = context.scroll_pane()
        if pane is None:
            context.dragging_scrollbar = False
            return False
        if event.event_type == MouseEventType.MOUSE_MOVE:
            current_y = getattr(event.position, "y", 0)
            rows = context.viewport_rows()
            maximum = context.max_output_scroll()
            delta = int((current_y - context.drag_start_y) * maximum / rows) if rows else 0
            pane.vertical_scroll = max(0, min(maximum, pane.vertical_scroll + delta))
            context.drag_start_y = current_y
            get_app().invalidate()
            return True
        if event.event_type == MouseEventType.MOUSE_UP:
            context.dragging_scrollbar = False
            return True
        return False

    def _start_vertical_drag(self, event: MouseEvent) -> None:
        context = self._context
        pane = context.scroll_pane()
        if pane is None:
            return
        context.dragging_scrollbar = True
        context.drag_start_y = getattr(event.position, "y", 0)
        rows = context.viewport_rows()
        maximum = context.max_output_scroll()
        fraction = context.drag_start_y / rows if rows else 0
        pane.vertical_scroll = max(0, min(maximum, int(fraction * maximum)))
        get_app().invalidate()

    def _handle_horizontal_drag(self, event: MouseEvent) -> bool:
        context = self._context
        if context.drag_start_x is None or context.should_wrap_lines():
            return False
        if event.event_type == MouseEventType.MOUSE_MOVE:
            current_x = getattr(event.position, "x", 0)
            context.apply_horizontal_scroll(context.drag_start_x - current_x)
            context.drag_start_x = current_x
            get_app().invalidate()
            return True
        if event.event_type == MouseEventType.MOUSE_UP:
            context.drag_start_x = None
            return True
        return False

    def _selection_target(self, event: MouseEvent) -> tuple[int, int] | None:
        context = self._context
        if not context.transcript or context.scroll_pane() is None:
            return None
        x = getattr(event.position, "x", 0)
        y = getattr(event.position, "y", 0)
        rows = context.viewport_rows()
        position = (context.scroll_pane().vertical_scroll + y) * context.viewport_cols() + x if rows else x
        accumulated = 0
        last_index = len(context.transcript) - 1
        target = (last_index, context.get_transcript_char_count(last_index))
        for index in range(len(context.transcript)):
            length = context.get_transcript_char_count(index)
            if accumulated + length > position:
                return index, max(0, min(length, position - accumulated))
            accumulated += length
        return target

    def _handle_copy(self, event: MouseEvent) -> NotImplementedOrNone:
        context = self._context
        target = self._selection_target(event)
        if target is None:
            return NotImplemented
        if event.event_type == MouseEventType.MOUSE_DOWN:
            context.selection_start[0] = context.selection_end[0] = target
            context.copy_mode_mouse_down[0] = True
        elif event.event_type == MouseEventType.MOUSE_MOVE and context.copy_mode_mouse_down[0]:
            context.selection_end[0] = target
            context.selection_text[0] = context.extract_selection_text()
        elif event.event_type == MouseEventType.MOUSE_UP:
            context.copy_mode_mouse_down[0] = False
            if context.selection_start[0] is not None:
                context.selection_end[0] = target
                context.selection_text[0] = context.extract_selection_text()
        else:
            return NotImplemented
        get_app().invalidate()
        return None


class _HorizontalScrollbarControl(UIControl):
    """渲染并处理 transcript 水平滚动条。"""

    def __init__(self, context: _ControlContext) -> None:
        self._context = context

    def preferred_width(self, max_available_width: int) -> int | None:
        return max_available_width

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        context = self._context
        return 1 if not context.should_wrap_lines() and context.max_horizontal_scroll() > 0 else 0

    def create_content(self, width: int, height: int) -> UIContent:
        fragments = self._render()
        return UIContent(get_line=lambda index: fragments if index == 0 else [], line_count=1)

    def _render(self) -> list[tuple[str, str]]:
        context = self._context
        maximum = context.max_horizontal_scroll()
        if context.should_wrap_lines() or maximum <= 0:
            return [("class:cli-spacer", "")]
        viewport = context.viewport_cols()
        current = context.horizontal_scroll[0]
        total = viewport + maximum
        thumb_width = max(2, int(viewport * viewport / total))
        thumb_position = min(viewport - thumb_width, int(viewport * current / total))
        result = [("class:hsb-arrow" if current else "class:hsb-arrow-disabled", "◀ " if current else "◁ ")]
        result.extend(
            ("class:hsb-thumb" if thumb_position <= index < thumb_position + thumb_width else "class:hsb-track", "█" if thumb_position <= index < thumb_position + thumb_width else "░")
            for index in range(viewport - 4)
        )
        result.append(("class:hsb-arrow" if current < maximum else "class:hsb-arrow-disabled", " ▶" if current < maximum else " ▷"))
        return result

    def mouse_handler(self, event: MouseEvent) -> NotImplementedOrNone:
        context = self._context
        maximum = context.max_horizontal_scroll()
        if context.should_wrap_lines() or maximum <= 0:
            return NotImplemented
        if event.event_type == MouseEventType.MOUSE_MOVE:
            return None
        if event.event_type != MouseEventType.MOUSE_DOWN:
            return NotImplemented
        viewport = context.viewport_cols()
        x = getattr(event.position, "x", 0)
        if x < 2:
            context.apply_horizontal_scroll(-20)
        elif x >= viewport - 2:
            context.apply_horizontal_scroll(20)
        else:
            track_width = viewport - 4
            if track_width > 0:
                context.horizontal_scroll[0] = max(0, min(maximum, int((x - 2) / track_width * maximum)))
                window = context.transcript_window_ref[0]
                if window is not None:
                    window.horizontal_scroll = context.horizontal_scroll[0]
        get_app().invalidate()
        return None


def create_transcript_controls(
    *,
    flatten_transcript_for_pt: Any,
    apply_horizontal_scroll: Any,
    apply_transcript_scroll: Any,
    copy_mode_active: list[bool],
    copy_mode_mouse_down: list[bool],
    extract_selection_text: Any,
    get_transcript_char_count: Any,
    is_scrollbar_click: Any,
    max_output_scroll: Any,
    scroll_pane: Any,
    selection_end: list[Any],
    selection_start: list[Any],
    selection_text: list[str],
    should_wrap_lines: Any,
    output_scroll_ref: list[Any],
    transcript: Any,
    transcript_window_ref: list[Any],
    viewport_cols: Any,
    viewport_rows: Any,
    wheel_line_step: Any,
    horizontal_scroll: list[int],
    max_horizontal_scroll: Any,
) -> tuple[Any, Any, Any, Any]:
    """构造 transcript 内容窗格、可滚动容器和水平滚动条。"""
    context = _ControlContext(locals())
    transcript_inner = FormattedTextControl(text=flatten_transcript_for_pt, focusable=False)
    transcript_window = Window(
        _TranscriptPaneControl(transcript_inner, context),
        wrap_lines=Condition(should_wrap_lines),
    )
    transcript_window_ref[0] = transcript_window
    output_scroll = ScrollablePane(
        transcript_window,
        height=D(weight=1),
        keep_cursor_visible=False,
        keep_focused_window_visible=False,
        show_scrollbar=True,
    )
    output_scroll_ref[0] = output_scroll
    horizontal_window = Window(
        _HorizontalScrollbarControl(context),
        dont_extend_width=True,
        dont_extend_height=True,
    )
    return transcript_inner, transcript_window, output_scroll, horizontal_window
__all__ = ["create_transcript_controls"]
