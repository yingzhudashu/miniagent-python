"""TUI 输出、思考流与渠道投递组合。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from prompt_toolkit.application import get_app

from miniagent.contracts.messages import OutboundEvent
from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.feishu_handler import create_feishu_handler
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import set_console_log_threshold

_ANSI_COLOR_STYLE_MAP: dict[str, str] = {
    "ansiblue": "class:cli-border",
    "ansigreen": "class:cli-ok",
    "ansired": "class:cli-err",
    "ansiyellow": "class:cli-warn",
    "ansicyan": "class:cli-user-title",
}


class _TuiOutputBindings:
    """拥有 TUI transcript、思考流和渠道适配器的组合生命周期。"""

    ctx: Any
    state: CliLoopState | dict[str, Any]
    engine: Any
    outbound_channels: Any
    cli_channel_adapter: Any
    coordinator: Any
    cli_outbound_dispatcher: Any
    build_cli_thinking_event: Any
    streaming_think_by_session: dict[str, Any]
    stream_state: Any
    transcript: Any
    stick_bottom: list[bool]
    safe_ansi: Any
    trim_transcript: Any
    append_transcript: Any
    append_ansi_transcript: Any
    markdown_render_width: Any
    output_at_bottom: Any
    snap_output_bottom: Any
    viewport_cols: Any
    rule_line_width_for_vp: Any
    reasoning_expanded: Any

    def __init__(self, **values: Any) -> None:
        """保存组合根注入的 UI 回调和状态引用。"""
        self.__dict__.update(values)
        self.ctx = values["runtime_context"]
        self.reasoning_expanded = values.get("reasoning_expanded", lambda: True)
        coordinator_class = values["transcript_coordinator_class"]
        self.coordinator = coordinator_class(
            self.append_transcript,
            self.append_ansi_transcript,
            on_turn_end=self._clear_stream_state,
        )

    def setup(self) -> SimpleNamespace:
        """注册渠道、显示宽度与思考 sink，并返回兼容绑定接口。"""
        self.ctx.cli_transcript_coordinator = self.coordinator
        self.ctx.create_feishu_handler_factory = lambda state: create_feishu_handler(
            state, self.ctx, self.stick_bottom
        )
        if not get_config("features.tui_verbose_log", False):
            set_console_log_threshold(logging.WARNING)
        self.engine.thinking.set_output_sink(self.thinking_sink)
        self.engine.thinking.set_cli_markdown_width(self.markdown_render_width)
        self.state["cli_render_width"] = self.rule_line_width
        self.state["cli_markdown_width"] = self.markdown_render_width
        adapter = self.cli_channel_adapter(
            self.deliver_cli_final,
            self.deliver_cli_error,
            self.deliver_cli_status,
            self.deliver_cli_thinking,
        )
        self.outbound_channels.register(adapter, replace=True)
        self.engine.thinking.set_output_sink(self.publish_cli_thinking)
        return SimpleNamespace(
            transcript_coordinator=self.coordinator,
            term_write=self.term_write,
            clear_cli_format_widths=self.clear_cli_format_widths,
            cli_rule_heavy=self.cli_rule_heavy,
            cli_rule_light=self.cli_rule_light,
            cli_block_user=self.cli_block_user,
            cli_block_reply=self.cli_block_reply,
            rule_line_width=self.rule_line_width,
        )

    def _clear_stream_state(self, session_key: str) -> None:
        self.streaming_think_by_session.pop(session_key, None)

    def term_write(self, text: str = "", color: str = "") -> None:
        """写入 transcript，优先 Markdown 渲染并安全回退。"""
        from miniagent.engine.markdown_cli import render_markdown_to_ansi

        if not text:
            return
        text = text if text.endswith("\n") else text + "\n"
        if color and not color.startswith(("class:", "ansi")):
            color = ""
        try:
            ansi_body = render_markdown_to_ansi(
                text, width=self.markdown_render_width(), justify="left"
            )
            if ansi_body is None:
                raise ValueError("Markdown renderer returned no output")
            self.transcript.extend(self.safe_ansi(ansi_body))
            self.trim_transcript()
        except Exception:
            self.append_transcript(_ANSI_COLOR_STYLE_MAP.get(color, "class:cli-default"), text)

    def _append_ansi_thinking(self, ansi_markdown: str) -> None:
        at_bottom = self.output_at_bottom()
        body = "\n".join(line or "" for line in ansi_markdown.rstrip("\n").split("\n")) + "\n"
        self.transcript.extend(self.safe_ansi(body))
        self.trim_transcript()
        self._invalidate()
        if at_bottom or self.stick_bottom[0]:
            self.snap_output_bottom()
            if at_bottom:
                self.stick_bottom[0] = True
        else:
            self.stick_bottom[0] = False

    def _append_streaming_thinking(self, fragment: str, session_key: str) -> None:
        from miniagent.engine.markdown_cli import render_markdown_to_ansi

        stream = self.stream_state(session_key)
        full_text = stream.text + fragment if stream.active else fragment
        safe_fragments = self.safe_ansi(
            render_markdown_to_ansi(
                full_text, width=self.markdown_render_width(), justify="left"
            ) or ""
        )
        if stream.active and stream.start_idx >= 0:
            while len(self.transcript) > stream.start_idx:
                self.transcript.pop()
            self.transcript.extend(safe_fragments)
        else:
            stream.start_idx = len(self.transcript)
            self.transcript.extend(safe_fragments)
        stream.text = full_text
        stream.active = True
        self._invalidate()
        if self.output_at_bottom() or self.stick_bottom[0]:
            self.snap_output_bottom()

    def thinking_sink_inner(
        self,
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        """按会话维护流式思考累积器。"""
        session_key = session_key.strip() or "default"
        stream = self.stream_state(session_key)
        if ansi_markdown is not None:
            if getattr(self, "reasoning_expanded", lambda: True)():
                self._append_ansi_thinking(ansi_markdown)
            return
        style = "class:cli-think-head" if kind == "label" else "class:cli-think-body"
        if kind == "label":
            stream.active = False
            stream.text = ""
            stream.start_idx = -1
            self.append_transcript(style, fragment)
            return
        if not getattr(self, "reasoning_expanded", lambda: True)():
            stream.text = stream.text + fragment if stream.active else fragment
            stream.active = True
            stream.start_idx = -1
            return
        try:
            self._append_streaming_thinking(fragment, session_key)
        except Exception:
            stream.active = False
            stream.text = ""
            stream.start_idx = -1
            self.append_transcript(style, fragment)

    @staticmethod
    def _invalidate() -> None:
        try:
            get_app().invalidate()
        except Exception:
            pass

    def thinking_sink(
        self,
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        """经 turn coordinator 路由思考片段。"""
        session_key = session_key.strip() or "default"
        def callback() -> None:
            self.thinking_sink_inner(
                fragment, kind, session_key=session_key, ansi_markdown=ansi_markdown
            )
        if self.coordinator.is_live(session_key):
            callback()
        else:
            self.coordinator.defer(session_key, callback)

    def rule_line_width(self) -> int:
        return self.rule_line_width_for_vp(self.viewport_cols())

    def clear_cli_format_widths(self) -> None:
        self.state.pop("cli_render_width", None)
        self.state.pop("cli_markdown_width", None)

    def cli_rule_heavy(self) -> None:
        self.append_transcript("class:cli-border-strong", "═" * self.rule_line_width() + "\n")

    def cli_rule_light(self) -> None:
        self.append_transcript("class:cli-border", "─" * self.rule_line_width() + "\n")

    def cli_block_user(self, prompt: str) -> None:
        from miniagent.engine.cli_format import format_cli_user_block

        format_cli_user_block(
            self.append_transcript,
            prompt,
            self.stick_bottom,
            render_width=self.rule_line_width(),
        )

    def cli_block_reply(self, text: str) -> None:
        from miniagent.engine.cli_format import format_cli_reply_block

        format_cli_reply_block(
            self.append_transcript,
            self.append_ansi_transcript,
            text,
            render_width=self.rule_line_width(),
            markdown_width=self.markdown_render_width(),
        )

    def deliver_cli_final(self, session_key: str, text: str) -> None:
        from miniagent.engine.cli_format import format_cli_reply_block

        format_cli_reply_block(
            self.coordinator.make_session_append(session_key),
            self.coordinator.make_session_append_ansi(session_key),
            text,
            render_width=self.rule_line_width(),
            markdown_width=self.markdown_render_width(),
        )

    def deliver_cli_error(self, _session_key: str, text: str) -> None:
        self.append_transcript("class:cli-err", text)

    def deliver_cli_status(self, _session_key: str, text: str) -> None:
        self.term_write(text)

    def deliver_cli_thinking(self, event: OutboundEvent) -> None:
        ansi_value = event.metadata.get("ansi_markdown")
        self.thinking_sink(
            event.content,
            str(event.metadata.get("fragment_kind") or "chunk"),
            session_key=event.target.conversation_id,
            ansi_markdown=ansi_value if isinstance(ansi_value, str) else None,
        )

    def publish_cli_thinking(
        self,
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        task = self.cli_outbound_dispatcher.publish(
            self.build_cli_thinking_event(
                fragment,
                session_key.strip() or "default",
                interface="tui",
                fragment_kind=kind,
                ansi_markdown=ansi_markdown,
            )
        )
        self.ctx.register_shutdown_tracked_task(task)


def create_tui_output_bindings(
    *,
    runtime_context: Any,
    state: CliLoopState | dict[str, Any],
    engine: Any,
    outbound_channels: Any,
    cli_channel_adapter: Any,
    transcript_coordinator_class: Any,
    cli_outbound_dispatcher: Any,
    build_cli_thinking_event: Any,
    streaming_think_by_session: dict[str, Any],
    stream_state: Any,
    transcript: Any,
    stick_bottom: list[bool],
    safe_ansi: Any,
    trim_transcript: Any,
    append_transcript: Any,
    append_ansi_transcript: Any,
    markdown_render_width: Any,
    output_at_bottom: Any,
    snap_output_bottom: Any,
    viewport_cols: Any,
    rule_line_width_for_vp: Any,
    reasoning_expanded: Any = lambda: True,
) -> SimpleNamespace:
    """注册 CLI 出站适配器并返回 transcript 输出闭包。"""
    return _TuiOutputBindings(
        runtime_context=runtime_context,
        state=state,
        engine=engine,
        outbound_channels=outbound_channels,
        cli_channel_adapter=cli_channel_adapter,
        transcript_coordinator_class=transcript_coordinator_class,
        cli_outbound_dispatcher=cli_outbound_dispatcher,
        build_cli_thinking_event=build_cli_thinking_event,
        streaming_think_by_session=streaming_think_by_session,
        stream_state=stream_state,
        transcript=transcript,
        stick_bottom=stick_bottom,
        safe_ansi=safe_ansi,
        trim_transcript=trim_transcript,
        append_transcript=append_transcript,
        append_ansi_transcript=append_ansi_transcript,
        markdown_render_width=markdown_render_width,
        output_at_bottom=output_at_bottom,
        snap_output_bottom=snap_output_bottom,
        viewport_cols=viewport_cols,
        rule_line_width_for_vp=rule_line_width_for_vp,
        reasoning_expanded=reasoning_expanded,
    ).setup()
__all__ = ["create_tui_output_bindings"]
