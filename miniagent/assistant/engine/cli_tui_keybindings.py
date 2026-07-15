"""全屏 TUI 键绑定注册器。"""

from __future__ import annotations

from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.cli_history import sync_preload_buffer_working_lines
from miniagent.assistant.engine.cli_shell import run_cli_shell_command
from miniagent.ui.cli.keybindings import DEFAULT_TUI_KEYBINDINGS
from miniagent.ui.tui.clipboard import copy_text_to_system_clipboard


class _TuiKeyBindingInstaller:
    """注册并处理 TUI 输入、复制、滚动和历史快捷键。"""

    kb: Any
    has_focus: Any
    condition: Any
    keys: Any
    input_buffer: Any
    toggle_copy_mode: Any
    copy_mode_active: list[bool]
    append_transcript: Any
    stick_bottom: list[bool]
    clear_selection: Any
    selection_text: list[str]
    selection_start: list[Any]
    selection_end: list[Any]
    transcript: Any
    rendered_text_length: Any
    has_selection: Any
    extract_selection_text: Any
    reset_and_reload_transcript: Any
    ctx: Any
    term_write: Any
    viewport_rows: Any
    apply_transcript_scroll: Any
    should_wrap_lines: Any
    apply_horizontal_scroll: Any
    horizontal_scroll: list[int]
    max_horizontal_scroll: Any
    wheel_line_step: Any
    request_model_palette: Any
    request_session_palette: Any
    toggle_reasoning: Any
    keymap: dict[str, str]

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)
        self.ctx = values["runtime_context"]

    def install(self) -> None:
        """将具名处理器注册到 prompt_toolkit。"""
        focus = self.has_focus(self.input_buffer)
        copy_mode_filter = self.condition(self.in_copy_mode)
        copy_filter = self.condition(self.copy_command_active)
        specs = (
            ("tab", self.on_tab, focus, False),
            ("s-tab", self.on_shift_tab, focus, False),
            (self.keymap["copy_mode"], self.on_ctrl_m, None, False),
            ("c-c", self.on_copy_ctrl_c, copy_filter, True),
            ("enter", self.on_copy_enter, copy_mode_filter, True),
            ("escape", self.on_copy_escape, copy_filter, True),
            ("a", self.on_copy_select_all, copy_mode_filter, True),
            ("enter", self.on_enter, focus, False),
            ("c-c", self.on_exit, focus, False),
            ("c-d", self.on_exit, focus, False),
            ("c-l", self.on_clear, focus, False),
            (self.keymap["tasks"], self.on_tasks, focus, False),
            (self.keymap["model_selector"], self.on_model_palette, focus, False),
            (self.keymap["session_selector"], self.on_session_palette, focus, False),
            (self.keymap["toggle_reasoning"], self.on_toggle_reasoning, focus, False),
            ("pageup", self.on_pageup, focus, False),
            ("pagedown", self.on_pagedown, focus, False),
            ("s-left", self.on_shift_left, focus, False),
            ("s-right", self.on_shift_right, focus, False),
            ("c-home", self.on_ctrl_home, focus, False),
            ("c-end", self.on_ctrl_end, focus, False),
            (self.keys.ScrollUp, self.on_scroll_up, focus, True),
            (self.keys.ScrollDown, self.on_scroll_down, focus, True),
            ("up", self.on_up, focus, False),
            ("down", self.on_down, focus, False),
        )
        for key, handler, filter_value, eager in specs:
            kwargs = {"eager": True} if eager else {}
            if filter_value is not None:
                kwargs["filter"] = filter_value
            self.kb.add(key, **kwargs)(handler)
        newline_keys = self.keymap["newline"].split()
        try:
            decorator = self.kb.add(*newline_keys, filter=focus)
        except TypeError:  # lightweight unit-test registries accept one key only
            decorator = self.kb.add("-".join(newline_keys), filter=focus)
        decorator(self.on_newline)

    def in_copy_mode(self) -> bool:
        return self.copy_mode_active[0]

    def copy_command_active(self) -> bool:
        return self.copy_mode_active[0] or self.has_selection()

    def on_tab(self, event: Any) -> None:
        event.app.current_buffer.start_completion()

    def on_shift_tab(self, event: Any) -> None:
        event.app.current_buffer.complete_previous()

    def on_ctrl_m(self, _event: Any) -> None:
        self.toggle_copy_mode()
        if self.copy_mode_active[0]:
            self.append_transcript(
                "class:cli-copy-mode-hint",
                "\n[复制模式] 拖动鼠标选择 · Ctrl+C复制 · Enter复制并退出 · Esc取消 · a全选 · Ctrl+M退出\n",
            )
            self.stick_bottom[0] = True
        else:
            self.clear_selection()

    def _copy_selected(self, *, exit_after: bool) -> None:
        text = self.selection_text[0]
        if text and copy_text_to_system_clipboard(text):
            suffix = "并退出复制模式" if exit_after else ""
            self.append_transcript("class:cli-ok", f"\n{SUCCESS_PREFIX} 已复制 {len(text)} 字符{suffix}\n")
        elif text:
            self.append_transcript("class:cli-err", f"\n{ERROR_PREFIX} 复制失败（剪贴板不可用）\n")
        else:
            self.append_transcript("class:cli-warn", f"\n{WARNING_PREFIX} 请先选择内容\n")
        self.stick_bottom[0] = True

    def on_copy_ctrl_c(self, _event: Any) -> None:
        self._copy_selected(exit_after=False)

    def on_copy_enter(self, _event: Any) -> None:
        self._copy_selected(exit_after=True)
        self.toggle_copy_mode()

    def on_copy_escape(self, _event: Any) -> None:
        if self.selection_start[0] is not None:
            self.clear_selection()
        elif self.copy_mode_active[0]:
            self.toggle_copy_mode()

    def on_copy_select_all(self, _event: Any) -> None:
        if not self.transcript:
            return
        length = self.rendered_text_length()
        if length <= 0:
            self.append_transcript("class:cli-warn", f"\n{WARNING_PREFIX} 内容为空\n")
            return
        self.selection_start[0] = 0
        self.selection_end[0] = length
        self.selection_text[0] = self.extract_selection_text()
        self.append_transcript(
            "class:cli-ok", f"\n{SUCCESS_PREFIX} 已全选 {len(self.selection_text[0])} 字符\n"
        )
        self.stick_bottom[0] = True

    def on_enter(self, event: Any) -> None:
        text = self.input_buffer.text.strip()
        if not text:
            return
        if text.startswith("!"):
            command = text[1:].strip()
            if command:
                ok, output = run_cli_shell_command(command)
                self.append_transcript("class:cli-default" if ok else "class:cli-err", output)
                self.stick_bottom[0] = True
                event.app.invalidate()
            self.input_buffer.reset(append_to_history=True)
            return
        self.input_buffer.reset(append_to_history=True)
        event.app.exit(result=text)

    def on_newline(self, event: Any) -> None:
        """Insert a literal newline; Enter remains the unambiguous submit key."""
        self.input_buffer.insert_text("\n")
        event.app.invalidate()

    def on_model_palette(self, event: Any) -> None:
        self.request_model_palette(event)

    def on_session_palette(self, event: Any) -> None:
        self.request_session_palette(event)

    def on_toggle_reasoning(self, event: Any) -> None:
        self.toggle_reasoning()
        event.app.invalidate()

    @staticmethod
    def on_exit(event: Any) -> None:
        event.app.exit(result="__exit__")

    def on_clear(self, event: Any) -> None:
        self.reset_and_reload_transcript(reset_scroll_to_top=True)
        event.app.invalidate()

    def on_tasks(self, event: Any) -> None:
        from miniagent.assistant.engine.btw_cmd import cmd_btw_status

        self.term_write(cmd_btw_status(self.ctx.background_tasks) + "\n", "ansicyan")
        self.stick_bottom[0] = True
        event.app.invalidate()

    def scroll_step(self) -> int:
        return max(1, self.viewport_rows() // 2)

    def on_pageup(self, event: Any) -> None:
        self.apply_transcript_scroll(-self.scroll_step(), "pageup")
        event.app.invalidate()

    def on_pagedown(self, event: Any) -> None:
        self.apply_transcript_scroll(self.scroll_step(), "pagedown")
        event.app.invalidate()

    def on_shift_left(self, event: Any) -> None:
        if not self.should_wrap_lines():
            self.apply_horizontal_scroll(-10)
            event.app.invalidate()

    def on_shift_right(self, event: Any) -> None:
        if not self.should_wrap_lines():
            self.apply_horizontal_scroll(10)
            event.app.invalidate()

    def on_ctrl_home(self, event: Any) -> None:
        self.input_buffer.cursor_position = 0
        event.app.invalidate()

    def on_ctrl_end(self, event: Any) -> None:
        self.input_buffer.cursor_position = len(self.input_buffer.text)
        event.app.invalidate()

    def on_scroll_up(self, event: Any) -> None:
        self.apply_transcript_scroll(-self.wheel_line_step(), "keys.ScrollUp")
        event.app.invalidate()

    def on_scroll_down(self, event: Any) -> None:
        self.apply_transcript_scroll(self.wheel_line_step(), "keys.ScrollDown")
        event.app.invalidate()

    def _ensure_history_ready(self) -> None:
        history = getattr(self.input_buffer, "history", None)
        get_strings = getattr(history, "get_strings", None)
        if get_strings and len(self.input_buffer._working_lines) <= 1 and get_strings():
            sync_preload_buffer_working_lines(self.input_buffer)

    def on_up(self, event: Any) -> None:
        if self.input_buffer.complete_state:
            self.input_buffer.complete_previous()
        else:
            self.input_buffer.load_history_if_not_yet_loaded()
            self._ensure_history_ready()
            self.input_buffer.history_backward()
        event.app.invalidate()

    def on_down(self, event: Any) -> None:
        if self.input_buffer.complete_state:
            self.input_buffer.complete_next()
        else:
            self.input_buffer.load_history_if_not_yet_loaded()
            self._ensure_history_ready()
            self.input_buffer.history_forward()
        event.app.invalidate()


def install_tui_key_bindings(
    *,
    kb: Any,
    has_focus: Any,
    condition: Any,
    keys: Any,
    input_buffer: Any,
    toggle_copy_mode: Any,
    copy_mode_active: list[bool],
    append_transcript: Any,
    stick_bottom: list[bool],
    clear_selection: Any,
    selection_text: list[str],
    selection_start: list[Any],
    selection_end: list[Any],
    transcript: Any,
    rendered_text_length: Any,
    has_selection: Any,
    extract_selection_text: Any,
    reset_and_reload_transcript: Any,
    runtime_context: Any,
    term_write: Any,
    viewport_rows: Any,
    apply_transcript_scroll: Any,
    should_wrap_lines: Any,
    apply_horizontal_scroll: Any,
    horizontal_scroll: list[int],
    max_horizontal_scroll: Any,
    wheel_line_step: Any,
    request_model_palette: Any = lambda _event: None,
    request_session_palette: Any = lambda _event: None,
    toggle_reasoning: Any = lambda: None,
    keymap: dict[str, str] | None = None,
) -> None:
    """注册输入、复制、滚动和历史导航快捷键。"""
    _TuiKeyBindingInstaller(
        kb=kb,
        has_focus=has_focus,
        condition=condition,
        keys=keys,
        input_buffer=input_buffer,
        toggle_copy_mode=toggle_copy_mode,
        copy_mode_active=copy_mode_active,
        append_transcript=append_transcript,
        stick_bottom=stick_bottom,
        clear_selection=clear_selection,
        selection_text=selection_text,
        selection_start=selection_start,
        selection_end=selection_end,
        transcript=transcript,
        rendered_text_length=rendered_text_length,
        has_selection=has_selection,
        extract_selection_text=extract_selection_text,
        reset_and_reload_transcript=reset_and_reload_transcript,
        runtime_context=runtime_context,
        term_write=term_write,
        viewport_rows=viewport_rows,
        apply_transcript_scroll=apply_transcript_scroll,
        should_wrap_lines=should_wrap_lines,
        apply_horizontal_scroll=apply_horizontal_scroll,
        horizontal_scroll=horizontal_scroll,
        max_horizontal_scroll=max_horizontal_scroll,
        wheel_line_step=wheel_line_step,
        request_model_palette=request_model_palette,
        request_session_palette=request_session_palette,
        toggle_reasoning=toggle_reasoning,
        keymap=keymap or DEFAULT_TUI_KEYBINDINGS,
    ).install()
    return
__all__ = ["install_tui_key_bindings"]
