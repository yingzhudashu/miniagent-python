"""TUI transcript、历史分页、选择与格式化控制器。"""

from __future__ import annotations

from typing import Any

from prompt_toolkit.application import get_app

from miniagent.agent.logging import get_logger
from miniagent.assistant.engine.cli_state import CliLoopState

_logger = get_logger(__name__)

_BORDER_CLASSES = frozenset({"class:cli-border", "class:cli-border-strong"})
_HRULE_CHARS = frozenset({"─", "═", "━"})


class _TranscriptOperations:
    """拥有 transcript 历史、选择与格式化状态的操作对象。"""

    state: CliLoopState | dict[str, Any]
    initial_history_count: int
    history_loaded_range: dict[str, Any]
    transcript: Any
    stick_bottom: list[bool]
    last_md_width: list[int]
    copy_mode_active: list[bool]
    copy_mode_mouse_down: list[bool]
    selection_start: list[Any]
    selection_end: list[Any]
    selection_text: list[str]
    is_valid_pt_style: Any
    safe_ansi: Any
    sp: Any
    viewport_cols: Any
    append_transcript: Any
    markdown_render_width: Any
    cli_block_user: Any
    cli_block_reply: Any
    should_wrap_lines: Any
    reset_horizontal_scroll: Any
    snap_output_bottom: Any
    report_content_width: Any

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)
        self._rendered_text = ""
        self._rendered_lines = [""]
        self._rendered_line_offsets = [0]

    @staticmethod
    def transcript_fragment_len(fragment: Any) -> int:
        from miniagent.ui.tui.transcript import transcript_fragment_len

        return transcript_fragment_len(fragment)

    def trim_transcript(self) -> None:
        self.transcript.trim()

    def transcript_prepend(self, style: Any, text: str) -> None:
        self.clear_selection()
        self.transcript.prepend((style, text))

    def render_history_message(
        self, message: dict, prepend: bool = False, *, plain_text: bool = False
    ) -> None:
        """将一条会话历史渲染到 transcript 顶部或底部。"""
        role = message.get("role", "")
        content = message.get("content", "")
        if not content:
            return
        from miniagent.ui.tui.transcript import lines_for_prepend, rule_line_width

        rule_width = rule_line_width(self.viewport_cols())
        if role == "user":
            if prepend:
                for line in lines_for_prepend(content):
                    self.transcript_prepend("class:cli-user-body", line + "\n")
                for style, text in (
                    ("class:cli-user-title", "You\n"),
                    ("class:cli-border", "─" * rule_width + "\n"),
                    ("class:cli-border-strong", "═" * rule_width + "\n"),
                    ("class:cli-spacer", "\n"),
                ):
                    self.transcript_prepend(style, text)
            else:
                self.cli_block_user(content)
            return
        if role == "thinking":
            if prepend:
                self.transcript_prepend("class:cli-think-head", "💭 Thinking\n")
                self.transcript_prepend("class:cli-spacer", "\n")
            else:
                self.append_transcript("class:cli-think-head", "💭 Thinking\n")
            return
        if role != "assistant":
            return
        if plain_text and not prepend:
            self.append_transcript("class:cli-assistant-title", "Assistant\n")
            for line in content.splitlines() or [content]:
                self.append_transcript("class:cli-assistant-body", line + "\n")
            self.append_transcript("class:cli-border", "─" * rule_width + "\n")
        elif prepend:
            from miniagent.assistant.engine.markdown_cli import render_markdown_to_ansi

            ansi = render_markdown_to_ansi(
                content, width=self.markdown_render_width(), justify="left"
            )
            if ansi:
                for style, text in reversed(self.safe_ansi(ansi)):
                    self.transcript_prepend(style, text)
            else:
                for line in lines_for_prepend(content):
                    self.transcript_prepend("class:cli-assistant-body", line + "\n")
            self.transcript_prepend("class:cli-assistant-title", "Assistant\n")
            self.transcript_prepend("class:cli-border", "─" * rule_width + "\n")
        else:
            self.cli_block_reply(content)

    def load_initial_history(self) -> None:
        """读取当前会话最近一页历史并初始化分页状态。"""
        manager = self.state.get("session_manager")
        session_id = self.state.get("active_session_id", "")
        if not manager or not session_id:
            _logger.warning("历史加载失败: 会话上下文未设置")
            return
        try:
            messages, total = manager.load_session_history_range(
                session_id, start_idx=0, count=self.initial_history_count
            )
            from miniagent.ui.tui.transcript import (
                HISTORY_HINT_STYLE,
                history_all_loaded,
                history_load_hint,
                history_loaded_end,
                history_remaining,
            )

            end = history_loaded_end(0, len(messages), total)
            self.history_loaded_range.update(
                total_messages=total,
                loaded_start=0,
                loaded_end=end,
                all_loaded=history_all_loaded(total, end),
            )
            for message in list(messages or []):
                self.render_history_message(message, plain_text=True)
            if not self.history_loaded_range["all_loaded"]:
                self.transcript_prepend(
                    HISTORY_HINT_STYLE, history_load_hint(history_remaining(total, end))
                )
        except Exception as error:
            _logger.exception("历史加载异常: %s", error)

    def reset_and_reload_transcript(self, *, reset_scroll_to_top: bool = False) -> None:
        """清空 transcript 并重新加载当前会话首页。"""
        self.clear_selection()
        self.transcript.clear()
        self.history_loaded_range.update(
            total_messages=0, loaded_start=0, loaded_end=0, all_loaded=False, loading=False
        )
        if reset_scroll_to_top:
            scroll_pane = self.sp()
            if scroll_pane is not None:
                scroll_pane.vertical_scroll = 0
            self.reset_horizontal_scroll()
        self.load_initial_history()
        self.stick_bottom[0] = True
        try:
            self.snap_output_bottom()
            app = get_app()
            if getattr(app, "is_running", False):
                app.invalidate()
        except Exception:
            pass

    def trigger_lazy_load_more_history(self) -> None:
        """防重入地在 transcript 顶部加载下一页更旧历史。"""
        page = self.history_loaded_range
        if page["loading"] or page["all_loaded"]:
            return
        page["loading"] = True
        try:
            manager = self.state.get("session_manager")
            session_id = self.state.get("active_session_id", "")
            if not manager or not session_id:
                return
            start = page["loaded_end"]
            messages, total = manager.load_session_history_range(
                session_id, start_idx=start, count=page["batch_size"]
            )
            if not messages:
                page["all_loaded"] = True
                return
            if self.transcript and isinstance(self.transcript[0], tuple):
                if "加载更多历史" in self.transcript[0][1]:
                    self.transcript.popleft()
            from miniagent.ui.tui.transcript import (
                HISTORY_HINT_STYLE,
                history_all_loaded,
                history_load_hint,
                history_loaded_end,
                history_remaining,
                messages_for_prepend,
            )

            for message in messages_for_prepend(messages):
                self.render_history_message(message, prepend=True)
            end = history_loaded_end(start, len(messages), total)
            page["loaded_end"] = end
            page["all_loaded"] = history_all_loaded(total, end)
            if not page["all_loaded"]:
                self.transcript_prepend(
                    HISTORY_HINT_STYLE, history_load_hint(history_remaining(total, end))
                )
            get_app().invalidate()
        finally:
            page["loading"] = False

    def recheck_md_width(self) -> None:
        """终端宽度变化时重渲染保留源 Markdown 的 ANSI 条目。"""
        try:
            width = self.viewport_cols()
        except Exception:
            return
        if self.last_md_width[0] and width == self.last_md_width[0]:
            return
        if self.last_md_width[0]:
            self.clear_selection()
        self.last_md_width[0] = width
        if self.should_wrap_lines():
            self.reset_horizontal_scroll()
        if not self.transcript:
            return
        from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI

        from miniagent.assistant.engine.markdown_cli import render_markdown_to_ansi

        for fragment in self.transcript:
            if isinstance(fragment, PTANSI) and getattr(fragment, "_source_md", None):
                ansi = render_markdown_to_ansi(
                    fragment._source_md, width=self.markdown_render_width(), justify="left"
                )
                if ansi is not None:
                    fragment.value = ansi
        # 此方法由当前渲染调用；这里再次 invalidate 会形成尺寸变化重绘循环。

    def _truncate_formatted(self, items: list[Any], viewport: int) -> list[Any]:
        result = []
        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                result.append(item)
                continue
            style, text = item[0], item[1]
            style = style if self.is_valid_pt_style(style) else ""
            if self._is_hrule(text.rstrip("\n")):
                text = self._border_truncate(text, viewport)
            result.append((style, text))
        return result

    @staticmethod
    def _is_hrule(text: str) -> bool:
        return bool(text) and sum(char in _HRULE_CHARS for char in text) >= len(text) * 0.8

    @staticmethod
    def _border_truncate(text: str, viewport: int) -> str:
        safe = max(1, viewport // 2)
        if len(text) <= safe + 1:
            return text
        return text[:safe].rstrip("\n") + ("\n" if text.endswith("\n") else "")

    def get_transcript_fragment_text(self, index: int) -> str:
        if index < 0 or index >= len(self.transcript):
            return ""
        from miniagent.ui.tui.transcript import transcript_fragment_text

        return transcript_fragment_text(self.transcript[index])

    def get_transcript_char_count(self, index: int) -> int:
        return len(self.get_transcript_fragment_text(index))

    def _ordered_selection(self) -> tuple[int | None, int | None]:
        start, end = self.selection_start[0], self.selection_end[0]
        if start is not None and end is not None and start > end:
            return end, start
        return start, end

    def extract_selection_text(self) -> str:
        start, end = self._ordered_selection()
        if start is None or end is None:
            return ""
        return self._rendered_text[max(0, start) : min(len(self._rendered_text), end)]

    def rendered_position_to_offset(self, row: int, column: int) -> int:
        """把 FormattedTextControl 的逻辑行列转换为渲染文本绝对偏移。"""
        if not self._rendered_lines:
            return 0
        row = max(0, min(int(row), len(self._rendered_lines) - 1))
        line = self._rendered_lines[row]
        column = max(0, min(int(column), len(line)))
        return min(len(self._rendered_text), self._rendered_line_offsets[row] + column)

    def rendered_text_length(self) -> int:
        return len(self._rendered_text)

    def has_selection(self) -> bool:
        return bool(self.selection_text[0])

    def clear_selection(self) -> None:
        self.selection_start[0] = self.selection_end[0] = None
        self.selection_text[0] = ""
        self.copy_mode_mouse_down[0] = False

    def toggle_copy_mode(self) -> None:
        self.copy_mode_active[0] = not self.copy_mode_active[0]
        if not self.copy_mode_active[0]:
            self.clear_selection()
        try:
            get_app().invalidate()
        except Exception:
            pass

    def apply_selection_highlight(self, items: list[Any]) -> list[Any]:
        """按渲染文本绝对偏移切分片段，同时保留原有 Markdown/ANSI 样式。"""
        start, end = self._ordered_selection()
        if start is None or end is None or start >= end:
            return items
        result: list[Any] = []
        offset = 0
        for item in items:
            if not isinstance(item, tuple) or len(item) < 2:
                result.append(item)
                continue
            style, text = item[0], item[1]
            item_end = offset + len(text)
            left = max(0, start - offset)
            right = min(len(text), end - offset)
            if left >= right or item_end <= start or offset >= end:
                result.append((style, text))
            else:
                if left:
                    result.append((style, text[:left]))
                result.append(("class:cli-selection", text[left:right]))
                if right < len(text):
                    result.append((style, text[right:]))
            offset = item_end
        return result

    def _cache_rendered_text(self, output: list[Any]) -> None:
        from prompt_toolkit.formatted_text import split_lines
        from prompt_toolkit.formatted_text.utils import fragment_list_width

        self._rendered_text = "".join(
            item[1] for item in output if isinstance(item, tuple) and len(item) >= 2
        )
        lines = list(split_lines(output))
        self._rendered_lines = [
            "".join(item[1] for item in line if isinstance(item, tuple) and len(item) >= 2)
            for line in lines
        ] or [""]
        offsets: list[int] = []
        offset = 0
        for line in self._rendered_lines:
            offsets.append(offset)
            offset += len(line) + 1
        self._rendered_line_offsets = offsets
        self.report_content_width(max((fragment_list_width(line) for line in lines), default=0))

    def flatten_transcript_for_pt(self) -> list[Any]:
        """把混合 tuple/ANSI transcript 展开为安全 formatted text。"""
        self.recheck_md_width()
        from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI
        from prompt_toolkit.formatted_text.base import to_formatted_text

        viewport = self.viewport_cols()
        output = []
        for fragment in self.transcript:
            if isinstance(fragment, tuple) and len(fragment) >= 2:
                style, text = fragment[0], fragment[1]
                style = style if self.is_valid_pt_style(style) else ""
                if style in _BORDER_CLASSES:
                    text = self._border_truncate(text, viewport)
                output.append((style, text))
            elif isinstance(fragment, PTANSI):
                output.extend(self._truncate_formatted(to_formatted_text(fragment), viewport))
            else:
                output.extend(self._truncate_formatted(to_formatted_text(fragment), viewport))
        self._cache_rendered_text(output)
        start, end = self._ordered_selection()
        if start is not None and end is not None:
            if start > len(self._rendered_text) or end > len(self._rendered_text):
                self.clear_selection()
            else:
                output = self.apply_selection_highlight(output)
        return output


def create_transcript_operations(
    *,
    state: CliLoopState | dict[str, Any],
    initial_history_count: int,
    history_loaded_range: dict[str, Any],
    transcript: Any,
    stick_bottom: list[bool],
    last_md_width: list[int],
    copy_mode_active: list[bool],
    copy_mode_mouse_down: list[bool],
    selection_start: list[Any],
    selection_end: list[Any],
    selection_text: list[str],
    is_valid_pt_style: Any,
    safe_ansi: Any,
    sp: Any,
    viewport_cols: Any,
    append_transcript: Any,
    markdown_render_width: Any,
    cli_block_user: Any,
    cli_block_reply: Any,
    should_wrap_lines: Any,
    reset_horizontal_scroll: Any,
    snap_output_bottom: Any,
    report_content_width: Any,
) -> _TranscriptOperations:
    """构造共享同一 transcript 状态的一组闭包操作。"""
    return _TranscriptOperations(
        state=state,
        initial_history_count=initial_history_count,
        history_loaded_range=history_loaded_range,
        transcript=transcript,
        stick_bottom=stick_bottom,
        last_md_width=last_md_width,
        copy_mode_active=copy_mode_active,
        copy_mode_mouse_down=copy_mode_mouse_down,
        selection_start=selection_start,
        selection_end=selection_end,
        selection_text=selection_text,
        is_valid_pt_style=is_valid_pt_style,
        safe_ansi=safe_ansi,
        sp=sp,
        viewport_cols=viewport_cols,
        append_transcript=append_transcript,
        markdown_render_width=markdown_render_width,
        cli_block_user=cli_block_user,
        cli_block_reply=cli_block_reply,
        should_wrap_lines=should_wrap_lines,
        reset_horizontal_scroll=reset_horizontal_scroll,
        snap_output_bottom=snap_output_bottom,
        report_content_width=report_content_width,
    )
__all__ = ["create_transcript_operations"]
