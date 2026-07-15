"""prompt_toolkit 全屏应用的对象化组合根。"""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

from miniagent.agent.logging import set_console_log_threshold
from miniagent.assistant.engine.cli_history import create_cli_file_history, reload_cli_input_history
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.ui.tui.transcript import TranscriptBuffer


def _create_input_prompt(input_buffer: Any) -> Any:
    """创建按视觉行数增长、最多占用六行的输入区。"""
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.layout import VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import LayoutDimension as D

    return VSplit(
        [
            Window(
                FormattedTextControl(
                    HTML("<prompt-prefix>❯ </prompt-prefix><cli-muted>↑↓历史</cli-muted>")
                ),
                width=D.exact(4),
                dont_extend_height=True,
            ),
            Window(
                BufferControl(buffer=input_buffer),
                wrap_lines=True,
                height=D(min=1, max=6),
                dont_extend_height=True,
            ),
        ]
    )


class _TuiApplication:
    """持有全屏 CLI 的控件、transcript、输出接线与关闭生命周期。"""

    def __init__(
        self,
        ctx: Any,
        state: Any,
        skill_toolboxes: list[Any],
        skill_prompts: list[Any],
        *,
        history_file: str,
        unregister: Any,
    ) -> None:
        self.ctx = ctx
        self.state = state
        self.skill_toolboxes = skill_toolboxes
        self.skill_prompts = skill_prompts
        self.history_file = history_file
        self.unregister = unregister
        self.engine = ctx.engine
        self.registry = ctx.registry
        self.monitor = ctx.monitor
        self.channel_router = ctx.channel_router
        self.outbound_channels = ctx.outbound_channels
        self.streaming_states: dict[str, Any] = {}
        self.stick_bottom = [True]
        self.last_md_width = [0]
        self.copy_mode_active = [False]
        self.copy_mode_mouse_down = [False]
        self.selection_start: list[int | None] = [None]
        self.selection_end: list[int | None] = [None]
        self.selection_text = [""]
        self.history_range: dict[str, Any] = {
            "total_messages": 0,
            "loaded_start": 0,
            "loaded_end": 0,
            "batch_size": 3,
            "all_loaded": False,
            "loading": False,
        }
        self.term_write: Any = None
        self.cli_block_user: Any = None
        self.cli_block_reply: Any = None
        self.append_transcript: Any = None
        self.append_ansi_transcript: Any = None
        self.trigger_lazy_load: Any = lambda: None
        from miniagent.ui.cli.state import TuiTheme, TuiViewState

        theme_value = str(get_config("cli.theme", "auto"))
        self.view_state = TuiViewState(
            theme=cast(
                TuiTheme,
                theme_value if theme_value in ("auto", "dark", "light") else "auto",
            )
        )

    def stream_state(self, session_key: str = "") -> Any:
        """按会话获取隔离的流式 Markdown 片段状态。"""
        from miniagent.assistant.engine.cli_tui import _StreamingThinkState

        key = (session_key or "").strip() or "default"
        return self.streaming_states.setdefault(key, _StreamingThinkState())

    def skill_toolbox_snapshot(self) -> list[Any]:
        """优先返回热加载状态中的技能工具箱。"""
        from miniagent.assistant.skills.snapshots import get_skill_toolboxes_from_state

        return get_skill_toolboxes_from_state(self.state) or self.skill_toolboxes

    def skill_prompt_snapshot(self) -> str | None:
        """优先返回热加载状态中的技能提示拼接文本。"""
        from miniagent.assistant.skills.snapshots import (
            get_skill_prompts_from_state,
            join_skill_prompts,
        )

        return join_skill_prompts(get_skill_prompts_from_state(self.state) or self.skill_prompts)

    def setup_messaging(self) -> None:
        """建立 CLI 入站串行协调器和有序出站分派器。"""
        from miniagent.assistant.application.messaging import (
            InboundTurnCoordinator,
            OrderedOutboundDispatcher,
        )
        from miniagent.assistant.engine.cli_inbound import CLI_CONVERSATION_ID

        self.inbound_turns = InboundTurnCoordinator(
            self.ctx.message_queue,
            queue_key=lambda _message: CLI_CONVERSATION_ID,
        )
        self.outbound_dispatcher = OrderedOutboundDispatcher(self.outbound_channels)
        self.ctx.cli_outbound_dispatcher = self.outbound_dispatcher

    def setup_input_and_transcript(self) -> None:
        """建立输入历史、显式字符上限 transcript 与视口状态。"""
        from prompt_toolkit.application import get_app
        from prompt_toolkit.buffer import Buffer

        from miniagent.agent.constants import MAX_TRANSCRIPT_CHARS
        from miniagent.assistant.engine.cli_completion import create_cli_completer
        from miniagent.assistant.engine.cli_tui import _TuiViewport
        from miniagent.assistant.engine.command_registry import COMMAND_REGISTRY

        self.input_buffer = Buffer(
            history=create_cli_file_history(self.history_file),
            completer=create_cli_completer(COMMAND_REGISTRY.names),
            complete_while_typing=False,
        )
        reload_cli_input_history(self.state, self.input_buffer, self.history_file)
        max_chars = int(get_config("memory.max_transcript_chars", MAX_TRANSCRIPT_CHARS))
        self.transcript = TranscriptBuffer(max_chars)
        self.viewport = _TuiViewport(
            get_app,
            self.stick_bottom,
            lambda: self.trigger_lazy_load(),
        )

    def markdown_width(self) -> int:
        """根据当前视口计算 Markdown 渲染宽度。"""
        from miniagent.agent.constants import CLI_WIDTH_MARGIN
        from miniagent.ui.tui.transcript import markdown_render_width

        return markdown_render_width(self.viewport.columns(), CLI_WIDTH_MARGIN)

    def setup_transcript_operations(self) -> None:
        """装配历史分页、选择、重渲染和 transcript 读写操作。"""
        from miniagent.assistant.engine.cli_tui_transcript_ops import create_transcript_operations
        from miniagent.ui.tui.transcript import is_valid_pt_style, safe_ansi_fragments

        self.safe_ansi = safe_ansi_fragments
        self.transcript_ops = create_transcript_operations(
            state=self.state,
            initial_history_count=int(get_config("memory.initial_history_count", 5)),
            history_loaded_range=self.history_range,
            transcript=self.transcript,
            stick_bottom=self.stick_bottom,
            last_md_width=self.last_md_width,
            copy_mode_active=self.copy_mode_active,
            copy_mode_mouse_down=self.copy_mode_mouse_down,
            selection_start=self.selection_start,
            selection_end=self.selection_end,
            selection_text=self.selection_text,
            is_valid_pt_style=is_valid_pt_style,
            safe_ansi=self.safe_ansi,
            sp=self.viewport.pane,
            viewport_cols=self.viewport.columns,
            append_transcript=lambda *args, **kwargs: self.append_transcript(*args, **kwargs),
            markdown_render_width=self.markdown_width,
            cli_block_user=lambda text: self.cli_block_user(text),
            cli_block_reply=lambda text: self.cli_block_reply(text),
            should_wrap_lines=self.viewport.should_wrap,
            reset_horizontal_scroll=self.viewport.reset_horizontal,
            snap_output_bottom=self.viewport.snap_bottom,
            report_content_width=self.viewport.report_content_width,
        )
        self.trigger_lazy_load = self.transcript_ops.trigger_lazy_load_more_history

    def setup_controls_and_appenders(self) -> None:
        """建立 transcript 控件、滚动条和保持字符计数的追加器。"""
        from miniagent.ui.tui.appenders import create_transcript_appenders
        from miniagent.ui.tui.controls import create_transcript_controls
        from miniagent.ui.tui.transcript import is_valid_pt_style

        controls = create_transcript_controls(
            flatten_transcript_for_pt=self.transcript_ops.flatten_transcript_for_pt,
            apply_horizontal_scroll=self.viewport.apply_horizontal,
            apply_transcript_scroll=self.viewport.scroll,
            copy_mode_active=self.copy_mode_active,
            copy_mode_mouse_down=self.copy_mode_mouse_down,
            clear_selection=self.transcript_ops.clear_selection,
            extract_selection_text=self.transcript_ops.extract_selection_text,
            rendered_position_to_offset=self.transcript_ops.rendered_position_to_offset,
            rendered_text_length=self.transcript_ops.rendered_text_length,
            max_output_scroll=self.viewport.max_output,
            set_transcript_scroll=self.viewport.set_vertical,
            scroll_pane=self.viewport.pane,
            selection_end=self.selection_end,
            selection_start=self.selection_start,
            selection_text=self.selection_text,
            should_wrap_lines=self.viewport.should_wrap,
            output_scroll_ref=self.viewport.output_scroll_ref,
            transcript=self.transcript,
            transcript_window_ref=self.viewport.transcript_window_ref,
            viewport_cols=self.viewport.columns,
            viewport_rows=self.viewport.rows,
            wheel_line_step=self.viewport.wheel_step,
            horizontal_scroll=self.viewport.horizontal_scroll,
            max_horizontal_scroll=self.viewport.max_horizontal,
            begin_viewport_measure=self.viewport.begin_measure,
            finish_viewport_measure=self.viewport.finish_measure,
        )
        _, _, self.output_scroll, self.horizontal_scrollbar = controls
        appenders = create_transcript_appenders(
            is_valid_pt_style=is_valid_pt_style,
            output_at_bottom=self.viewport.at_bottom,
            transcript=self.transcript,
            trim_transcript=self.transcript_ops.trim_transcript,
            clear_selection=self.transcript_ops.clear_selection,
            stick_bottom=self.stick_bottom,
            snap_output_bottom=self.viewport.snap_bottom,
            safe_ansi=self.safe_ansi,
        )
        self.append_transcript = appenders.append_transcript
        self.transcript_plain = appenders.transcript_plain
        self.append_ansi_transcript = appenders.append_ansi_transcript

    def setup_keybindings(self) -> None:
        """安装补全、复制、滚动、清屏和 Shell 快捷键。"""
        from prompt_toolkit.filters import Condition, has_focus
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys

        from miniagent.assistant.engine.cli_tui_keybindings import install_tui_key_bindings
        from miniagent.ui.cli.keybindings import resolve_tui_keybindings

        self.key_bindings = KeyBindings()
        cli_config = get_config("cli", {})
        keymap = resolve_tui_keybindings(
            cli_config.get("keybindings") if isinstance(cli_config, dict) else None
        )
        install_tui_key_bindings(
            kb=self.key_bindings,
            has_focus=has_focus,
            condition=Condition,
            keys=Keys,
            input_buffer=self.input_buffer,
            toggle_copy_mode=self.transcript_ops.toggle_copy_mode,
            copy_mode_active=self.copy_mode_active,
            append_transcript=self.append_transcript,
            stick_bottom=self.stick_bottom,
            clear_selection=self.transcript_ops.clear_selection,
            selection_text=self.selection_text,
            selection_start=self.selection_start,
            selection_end=self.selection_end,
            transcript=self.transcript,
            rendered_text_length=self.transcript_ops.rendered_text_length,
            has_selection=self.transcript_ops.has_selection,
            extract_selection_text=self.transcript_ops.extract_selection_text,
            reset_and_reload_transcript=self.transcript_ops.reset_and_reload_transcript,
            runtime_context=self.ctx,
            term_write=lambda *args, **kwargs: self.term_write(*args, **kwargs),
            viewport_rows=self.viewport.rows,
            apply_transcript_scroll=self.viewport.scroll,
            should_wrap_lines=self.viewport.should_wrap,
            apply_horizontal_scroll=self.viewport.apply_horizontal,
            horizontal_scroll=self.viewport.horizontal_scroll,
            max_horizontal_scroll=self.viewport.max_horizontal,
            wheel_line_step=self.viewport.wheel_step,
            request_model_palette=lambda event: event.app.exit(
                result="__model_palette__"
            ),
            request_session_palette=lambda event: event.app.exit(
                result="__session_palette__"
            ),
            toggle_reasoning=self.view_state.toggle_reasoning,
            keymap=keymap,
        )

    def create_style(self) -> Any:
        """创建高对比滚动条与中文思考流样式。"""
        from prompt_toolkit.styles import Style

        from miniagent.agent.constants import CLI_STYLE_THINK_BODY, CLI_STYLE_THINK_HEAD

        return Style.from_dict(
            {
                "prompt-prefix": "bold ansigreen",
                "cli-border-strong": "ansibrightblue bold",
                "cli-border": "ansiblue dim",
                "cli-user-title": "bold ansicyan",
                "cli-user-body": "ansicyan",
                "cli-think-head": CLI_STYLE_THINK_HEAD,
                "cli-think-body": CLI_STYLE_THINK_BODY,
                "cli-assistant-title": "bold ansigreen",
                "cli-assistant-body": "ansigreen",
                "cli-default": "",
                "cli-muted": "ansibrightblack dim",
                "cli-ok": "ansigreen",
                "cli-err": "ansired bold",
                "cli-warn": "ansiyellow",
                "cli-hint": "ansibrightblack dim",
                "cli-footer": "ansibrightblack dim",
                "cli-spacer": "",
                "cli-selection": "bg:ansicyan fg:ansiblack bold",
                "cli-copy-mode-hint": "ansiyellow bold",
                "scrollbar.button": "bg:ansibrightcyan fg:ansiblack",
                "scrollbar.background": "bg:ansibrightblack",
                "scrollbar.arrow": "ansiwhite bold",
                "hsb-thumb": "bg:ansibrightcyan fg:ansiblack",
                "hsb-track": "bg:ansibrightblack",
                "hsb-arrow": "ansiwhite bold",
                "hsb-arrow-disabled": "ansibrightblack dim",
                "completion-menu": "bg:ansibrightblack fg:ansiwhite",
                "completion-menu.completion": "bg:ansibrightblack fg:ansiwhite",
                "completion-menu.completion.current": "bg:ansicyan fg:ansiblack bold",
                "completion-menu.meta": "bg:ansibrightblack fg:ansibrightblack dim",
                "completion-menu.meta.current": "bg:ansicyan fg:ansiblack dim",
            }
        )

    def setup_layout(self) -> None:
        """建立固定底部输入框、提示栏、滚动输出与补全浮层。"""
        from prompt_toolkit.application import Application
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import LayoutDimension as D
        from prompt_toolkit.layout.menus import CompletionsMenu

        from miniagent.ui.cli.components import footer_text

        hint = Window(
            FormattedTextControl(
                HTML(
                    "<cli-hint>PgUp/PgDn · 滚轮 · Shift+←/→ 水平滚动 · Ctrl+Home/End 移光标 · "
                    "Alt+Enter 换行 · Ctrl+P 模型 · Ctrl+O 会话 · Ctrl+R 推理 · "
                    "拖动选择 · Ctrl+C复制 · Ctrl+M复制模式</cli-hint>"
                )
            ),
            height=D.exact(1),
        )
        footer = Window(
            FormattedTextControl(
                text=lambda: [
                    (
                        "class:cli-footer",
                        footer_text(
                            self.ctx,
                            self.state,
                            self.view_state,
                            self.viewport.columns(),
                        ),
                    )
                ]
            ),
            height=D.exact(1),
        )
        prompt = _create_input_prompt(self.input_buffer)
        body = FloatContainer(
            HSplit(
                [
                    self.output_scroll,
                    self.horizontal_scrollbar,
                    footer,
                    hint,
                    Window(height=1, char="─", style="class:cli-border"),
                    prompt,
                ]
            ),
            floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=10))],
        )
        self.app = Application(
            layout=Layout(body, focused_element=self.input_buffer),
            key_bindings=self.key_bindings,
            full_screen=True,
            mouse_support=True,
            style=self.create_style(),
        )
        self.last_md_width[0] = self.viewport.columns()

    def setup_output_and_turn(self) -> None:
        """连接通道输出协调器与单轮 Agent 输入处理器。"""
        from miniagent.assistant.contracts.messages import OutboundEventKind
        from miniagent.assistant.engine.cli_inbound import build_cli_inbound_message
        from miniagent.assistant.engine.cli_outbound import (
            CliChannelAdapter,
            build_cli_outbound_event,
            build_cli_thinking_event,
        )
        from miniagent.assistant.engine.cli_tui_output import create_tui_output_bindings
        from miniagent.assistant.engine.cli_tui_turn import create_tui_process_input
        from miniagent.assistant.infrastructure.cli_transcript_coordinator import (
            CliTranscriptCoordinator,
        )
        from miniagent.ui.tui.transcript import rule_line_width

        self.ctx.cli_transcript_append = self.append_transcript
        self.ctx.cli_transcript_append_ansi = self.append_ansi_transcript
        output = create_tui_output_bindings(
            runtime_context=self.ctx,
            state=self.state,
            engine=self.engine,
            outbound_channels=self.outbound_channels,
            cli_channel_adapter=CliChannelAdapter,
            transcript_coordinator_class=CliTranscriptCoordinator,
            cli_outbound_dispatcher=self.outbound_dispatcher,
            build_cli_thinking_event=build_cli_thinking_event,
            streaming_think_by_session=self.streaming_states,
            stream_state=self.stream_state,
            transcript=self.transcript,
            stick_bottom=self.stick_bottom,
            safe_ansi=self.safe_ansi,
            trim_transcript=self.transcript_ops.trim_transcript,
            append_transcript=self.append_transcript,
            append_ansi_transcript=self.append_ansi_transcript,
            markdown_render_width=self.markdown_width,
            output_at_bottom=self.viewport.at_bottom,
            snap_output_bottom=self.viewport.snap_bottom,
            viewport_cols=self.viewport.columns,
            rule_line_width_for_vp=rule_line_width,
            reasoning_expanded=lambda: self.view_state.reasoning_expanded,
        )
        self.output_bindings = output
        self.term_write = output.term_write
        self.cli_block_user = output.cli_block_user
        self.cli_block_reply = output.cli_block_reply
        self.process_input = create_tui_process_input(
            channel_router=self.channel_router,
            state=self.state,
            runtime_context=self.ctx,
            term_write=self.term_write,
            transcript_coordinator=output.transcript_coordinator,
            engine=self.engine,
            cli_rule_heavy=output.cli_rule_heavy,
            output_at_bottom=self.viewport.at_bottom,
            stick_bottom=self.stick_bottom,
            snap_output_bottom=self.viewport.snap_bottom,
            rule_line_width=output.rule_line_width,
            skill_toolboxes=self.skill_toolbox_snapshot,
            skill_prompts=self.skill_prompt_snapshot,
            registry=self.registry,
            monitor=self.monitor,
            cli_outbound_dispatcher=self.outbound_dispatcher,
            outbound_channels=self.outbound_channels,
            build_cli_outbound_event=build_cli_outbound_event,
            outbound_event_kind=OutboundEventKind,
        )
        self.build_cli_inbound_message = build_cli_inbound_message
        self.build_cli_outbound_event = build_cli_outbound_event
        self.outbound_event_kind = OutboundEventKind

    def build(self) -> None:
        """按依赖顺序构建全屏应用，回调通过实例字段延迟解析。"""
        self.setup_messaging()
        self.setup_input_and_transcript()
        self.setup_transcript_operations()
        self.setup_controls_and_appenders()
        self.setup_keybindings()
        self.setup_layout()
        self.setup_output_and_turn()
        self.transcript_ops.reset_and_reload_transcript()

    def interaction_runtime(self) -> dict[str, Any]:
        """生成输入分类器所需的显式运行时映射。"""
        return {
            "app": self.app,
            "ctx": self.ctx,
            "state": self.state,
            "engine": self.engine,
            "registry": self.registry,
            "monitor": self.monitor,
            "channel_router": self.channel_router,
            "outbound_channels": self.outbound_channels,
            "inbound_turns": self.inbound_turns,
            "process_input": self.process_input,
            "build_cli_inbound_message": self.build_cli_inbound_message,
            "build_cli_outbound_event": self.build_cli_outbound_event,
            "outbound_event_kind": self.outbound_event_kind,
            "skill_tb": self.skill_toolbox_snapshot,
            "skill_toolboxes": self.skill_toolboxes,
            "skill_prompts": self.skill_prompts,
            "transcript_plain": self.transcript_plain,
            "term_write": self.term_write,
            "reset_transcript": self.transcript_ops.reset_and_reload_transcript,
            "input_buffer": self.input_buffer,
            "history_file": self.history_file,
            "clear_widths": self.output_bindings.clear_cli_format_widths,
            "open_model_palette": self.open_model_palette,
            "open_session_palette": self.open_session_palette,
            "interactive_background": True,
            "view_state": self.view_state,
        }

    async def open_model_palette(self) -> None:
        """Select and atomically activate the default role model."""
        from miniagent.assistant.engine.model_cmd import switch_model_profile
        from miniagent.assistant.infrastructure.json_config import reload_runtime_config
        from miniagent.ui.cli.model_selector import choose_model_profile

        profile = await choose_model_profile(self.ctx)
        if not profile:
            return
        descriptor = self.ctx.llm_gateway.catalog.get(profile)
        result = switch_model_profile(profile, descriptor=descriptor)
        if result.startswith("❌"):
            self.append_transcript("class:cli-err", f"\n{result}\n")
            return
        try:
            await reload_runtime_config(self.ctx)
        except Exception as error:
            self.append_transcript(
                "class:cli-err", f"\n模型切换验证失败: {error}\n"
            )
            return
        self.append_transcript("class:cli-ok", f"\n{result}\n")

    async def open_session_palette(self) -> str | None:
        """Return a selected session id; command dispatch performs the switch."""
        from miniagent.ui.cli.session_selector import choose_session

        return await choose_session(self.state)

    def close(self) -> None:
        """幂等释放渲染缓存、会话锁和实例登记。"""
        from miniagent.assistant.engine.session_continue import save_cli_session_state
        from miniagent.assistant.engine.session_lock import release_session_lock

        self.output_bindings.clear_cli_format_widths()
        set_console_log_threshold(logging.INFO)
        self.ctx.cli_transcript_append = None
        save_cli_session_state(self.ctx, self.state)
        release_session_lock(self.state["active_session_id"])
        try:
            self.unregister()
        except Exception:
            logging.getLogger(__name__).debug("注销实例失败", exc_info=True)
        print("\n\U0001f44b bye\n", file=sys.stdout, flush=True)


async def run_fullscreen_cli(
    ctx: Any,
    state: Any,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
    *,
    history_file: str,
    unregister: Any,
) -> None:
    """构建、运行并关闭全屏应用；交互异常回退时由输入循环负责清理。"""
    from miniagent.assistant.engine.cli_tui import _run_tui_interaction

    application = _TuiApplication(
        ctx,
        state,
        skill_toolboxes,
        skill_prompts,
        history_file=history_file,
        unregister=unregister,
    )
    application.build()
    fell_back = await _run_tui_interaction(**application.interaction_runtime())
    if not fell_back:
        application.close()


__all__ = ["run_fullscreen_cli"]
