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
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType, MouseModifier

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_bindings import NotImplementedOrNone


@dataclass
class _ControlContext:
    """保存 transcript 控件共享的回调与可变引用。"""

    values: dict[str, Any]
    drag_start_x: int | None = None
    selection_anchor: int | None = None
    selection_dragged: bool = False

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
        if event.event_type == MouseEventType.SCROLL_UP:
            context.apply_transcript_scroll(-context.wheel_line_step(), "mouse.SCROLL_UP")
            get_app().invalidate()
            return None
        if event.event_type == MouseEventType.SCROLL_DOWN:
            context.apply_transcript_scroll(context.wheel_line_step(), "mouse.SCROLL_DOWN")
            get_app().invalidate()
            return None
        handled = self._handle_horizontal_drag(event)
        if handled:
            return None
        if (
            not context.should_wrap_lines()
            and event.event_type == MouseEventType.MOUSE_DOWN
            and self._wants_horizontal_pan(event)
        ):
            context.drag_start_x = getattr(event.position, "x", 0)
            return None
        if event.event_type in {
            MouseEventType.MOUSE_DOWN,
            MouseEventType.MOUSE_MOVE,
            MouseEventType.MOUSE_UP,
        }:
            return self._handle_copy(event)
        return self._inner.mouse_handler(event)

    @staticmethod
    def _wants_horizontal_pan(event: MouseEvent) -> bool:
        modifiers = getattr(event, "modifiers", frozenset()) or frozenset()
        button = getattr(event, "button", None)
        return button == MouseButton.MIDDLE or MouseModifier.SHIFT in modifiers

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

    def _selection_target(self, event: MouseEvent) -> int | None:
        context = self._context
        if not context.transcript or context.scroll_pane() is None:
            return None
        # Window 已把屏幕坐标（含滚动、折行和宽字符）转换成 UIContent 的
        # 逻辑 row/column；这里不能再次叠加 ScrollablePane.vertical_scroll。
        return context.rendered_position_to_offset(
            getattr(event.position, "y", 0),
            getattr(event.position, "x", 0),
        )

    @staticmethod
    def _is_primary_button(event: MouseEvent) -> bool:
        return getattr(event, "button", None) in {
            None,
            MouseButton.LEFT,
            MouseButton.NONE,
            MouseButton.UNKNOWN,
        }

    def _update_selection(self, target: int) -> None:
        context = self._context
        anchor = context.selection_anchor
        if anchor is None:
            return
        length = context.rendered_text_length()
        if target >= anchor:
            context.selection_start[0] = anchor
            context.selection_end[0] = min(length, target + 1)
        else:
            context.selection_start[0] = min(length, anchor + 1)
            context.selection_end[0] = target
        context.selection_text[0] = context.extract_selection_text()

    def _handle_copy(self, event: MouseEvent) -> NotImplementedOrNone:
        context = self._context
        if not self._is_primary_button(event):
            return NotImplemented
        target = self._selection_target(event)
        if target is None:
            return NotImplemented
        if event.event_type == MouseEventType.MOUSE_DOWN:
            context.selection_start[0] = context.selection_end[0] = target
            context.copy_mode_mouse_down[0] = True
            context.selection_anchor = target
            context.selection_dragged = False
            context.selection_text[0] = ""
        elif event.event_type == MouseEventType.MOUSE_MOVE and context.copy_mode_mouse_down[0]:
            context.selection_dragged = context.selection_dragged or target != context.selection_anchor
            if context.selection_dragged:
                self._update_selection(target)
        elif event.event_type == MouseEventType.MOUSE_UP:
            context.copy_mode_mouse_down[0] = False
            if context.selection_anchor is not None and context.selection_dragged:
                self._update_selection(target)
            else:
                context.clear_selection()
            context.selection_anchor = None
            context.selection_dragged = False
        else:
            return NotImplemented
        get_app().invalidate()
        return None


class _MeasuredScrollablePane(ScrollablePane):
    """在绘制前发布真实几何数据，并为右侧滚动条补充鼠标交互。"""

    def __init__(self, content: Any, context: _ControlContext, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._context = context
        self._dragging_scrollbar = False

    def write_to_screen(
        self,
        screen: Any,
        mouse_handlers: Any,
        write_position: Any,
        parent_style: str,
        erase_bg: bool,
        z_index: int | None,
    ) -> None:
        scrollbar_width = 1 if self.show_scrollbar() else 0
        content_width = max(1, write_position.width - scrollbar_width)
        self._context.begin_viewport_measure(content_width, write_position.height)
        preferred = self.content.preferred_height(
            content_width, self.max_available_height
        ).preferred
        content_height = min(
            self.max_available_height,
            max(preferred, write_position.height),
        )
        self._context.finish_viewport_measure(content_height)
        super().write_to_screen(
            screen,
            mouse_handlers,
            write_position,
            parent_style,
            erase_bg,
            z_index,
        )
        if self.show_scrollbar() and write_position.width > 0 and write_position.height > 0:
            xpos = write_position.xpos + write_position.width - 1
            mouse_handlers.set_mouse_handler_for_range(
                x_min=xpos,
                x_max=xpos + 1,
                y_min=write_position.ypos,
                y_max=write_position.ypos + write_position.height,
                handler=lambda event: self._handle_scrollbar_mouse(event, write_position),
            )

    def _handle_scrollbar_mouse(self, event: MouseEvent, write_position: Any) -> None:
        event_type = event.event_type
        if event_type == MouseEventType.MOUSE_UP:
            self._dragging_scrollbar = False
            return None
        if event_type == MouseEventType.MOUSE_DOWN:
            self._dragging_scrollbar = True
        elif event_type != MouseEventType.MOUSE_MOVE or not self._dragging_scrollbar:
            return None
        local_y = max(0, min(write_position.height - 1, event.position.y - write_position.ypos))
        if self.display_arrows() and event_type == MouseEventType.MOUSE_DOWN:
            if local_y == 0:
                self._context.apply_transcript_scroll(-self._context.wheel_line_step(), "scrollbar.up")
                get_app().invalidate()
                return None
            if local_y == write_position.height - 1:
                self._context.apply_transcript_scroll(self._context.wheel_line_step(), "scrollbar.down")
                get_app().invalidate()
                return None
        inset = 1 if self.display_arrows() else 0
        track_height = max(1, write_position.height - inset * 2)
        fraction = max(0.0, min(1.0, (local_y - inset) / max(1, track_height - 1)))
        self._context.set_transcript_scroll(
            round(self._context.max_output_scroll() * fraction), "scrollbar.drag"
        )
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
    clear_selection: Any,
    extract_selection_text: Any,
    rendered_position_to_offset: Any,
    rendered_text_length: Any,
    max_output_scroll: Any,
    set_transcript_scroll: Any,
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
    begin_viewport_measure: Any,
    finish_viewport_measure: Any,
) -> tuple[Any, Any, Any, Any]:
    """构造 transcript 内容窗格、可滚动容器和水平滚动条。"""
    context = _ControlContext(locals())
    transcript_inner = FormattedTextControl(text=flatten_transcript_for_pt, focusable=False)
    transcript_window = Window(
        _TranscriptPaneControl(transcript_inner, context),
        wrap_lines=Condition(should_wrap_lines),
    )
    transcript_window_ref[0] = transcript_window
    output_scroll = _MeasuredScrollablePane(
        transcript_window,
        context,
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
