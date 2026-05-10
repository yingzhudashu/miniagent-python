"""Engine — 思考过程显示

拆分自 unified.py。

职责：
- 步骤编号（按会话隔离计数器）
- CLI 流式输出（写入 Application 输出缓冲区）
- 飞书会话：通过回调推送思考（与 CLI 终端输出策略不同）
- 多会话并发安全：每个会话独立状态，互不干扰
"""

from __future__ import annotations

import inspect
import logging
import sys
from typing import Awaitable, Callable

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text

_logger = logging.getLogger(__name__)

# 飞书发送回调类型
OnFeishuSend = Callable[[str, str, str], Awaitable[None]]
# 参数: (chat_id, thinking_text, template)


class _SessionThinkingState:
    """单个会话的思考状态（内部使用）。"""
    __slots__ = ("step_counter", "buffer", "feishu_send", "feishu_chat_id",
                 "stream_step", "stream_header", "stream_done", "stream_printed",
                 "stream_first_body_chunk")

    step_counter: int
    buffer: list[str]
    feishu_send: OnFeishuSend | None
    feishu_chat_id: str
    stream_step: int | None
    stream_header: str
    stream_done: bool
    stream_printed: int  # 已打印的字符数（用于增量输出）
    stream_first_body_chunk: bool  # 每轮流式正文首段首行缩进

    def __init__(self) -> None:
        self.step_counter = 0
        self.buffer = []
        self.feishu_send = None
        self.feishu_chat_id = ""
        self.stream_step = None
        self.stream_header = ""
        self.stream_done = False
        self.stream_printed = 0
        self.stream_first_body_chunk = True


class ThinkingDisplay:
    """思考过程显示（CLI 终端 + 飞书实时发送）

    CLI：流式输出到终端，原地更新。
    飞书侧会话：通过已注册的回调发送思考内容。
    """

    def __init__(self) -> None:
        self._states: dict[str, _SessionThinkingState] = {}
        self._default: _SessionThinkingState = _SessionThinkingState()
        self._buffer_enabled: bool = False
        # Application 输出缓冲区回调（用于全屏模式）
        self._output_sink: Callable[..., None] | None = None
        self._sink_has_kind: bool = False

    def set_output_sink(self, sink: Callable[..., None] | None) -> None:
        """设置输出目标（全屏模式写入 transcript，否则 None 走 print）。

        若 sink 接受第二参数 ``kind``（``"label"`` | ``"chunk"``），则用于分区着色。
        """
        self._output_sink = sink
        self._sink_has_kind = False
        if sink is not None:
            try:
                self._sink_has_kind = len(inspect.signature(sink).parameters) >= 2
            except (TypeError, ValueError):
                self._sink_has_kind = False

    def _emit(self, text: str, color: str = "ansigray") -> None:
        """统一输出入口。"""
        if self._output_sink:
            if self._sink_has_kind:
                self._output_sink(text, "chunk")
            else:
                self._output_sink(text)
        else:
            ft = FormattedText([(f"ansi{color}", text)])
            print_formatted_text(ft, end="")
            sys.stdout.flush()

    def _emit_line(self, text: str, color: str = "ansiblue") -> None:
        """统一换行输出入口。"""
        if self._output_sink:
            if self._sink_has_kind:
                self._output_sink(text + "\n", "label")
            else:
                self._output_sink(text + "\n")
        else:
            ft = FormattedText([(f"ansi{color}", text + "\n")])
            print_formatted_text(ft)
            sys.stdout.flush()

    @staticmethod
    def _indent_stream_body_paragraphs(new_text: str, state: _SessionThinkingState) -> str:
        """流式正文：首段首行 + 每个用 \\n\\n 分段后的段首行加缩进。"""
        t = new_text.replace("\r\n", "\n")
        parts = t.split("\n\n")
        out_parts: list[str] = []
        for j, part in enumerate(parts):
            lines = part.split("\n")
            if j == 0:
                if state.stream_first_body_chunk and lines and lines[0]:
                    lines[0] = "    " + lines[0]
                    state.stream_first_body_chunk = False
            elif lines and lines[0]:
                lines[0] = "    " + lines[0]
            out_parts.append("\n".join(lines))
        return "\n\n".join(out_parts)

    def _get_state(self, session_key: str) -> _SessionThinkingState:
        if session_key not in self._states:
            self._states[session_key] = _SessionThinkingState()
        return self._states[session_key]

    def reset_counter(self, session_key: str = "") -> None:
        state = self._get_state(session_key)
        state.step_counter = 0
        state.buffer.clear()
        state.feishu_send = None
        state.feishu_chat_id = ""
        state.stream_step = None
        state.stream_header = ""
        state.stream_done = False
        state.stream_printed = 0
        state.stream_first_body_chunk = True

    def enable_feishu(self, session_key: str, chat_id: str, send_callback: OnFeishuSend) -> None:
        state = self._get_state(session_key)
        state.feishu_chat_id = chat_id
        state.feishu_send = send_callback

    def enable_buffer(self) -> None:
        self._buffer_enabled = True
        self._default.buffer.clear()
        self._default.feishu_send = None

    def disable_buffer(self, session_key: str = "") -> None:
        if session_key:
            state = self._get_state(session_key)
            state.buffer.clear()
            state.feishu_send = None
            state.feishu_chat_id = ""
        else:
            self._buffer_enabled = False
            self._default.buffer.clear()
            self._default.feishu_send = None
            self._default.feishu_chat_id = ""

    def get_buffered(self, session_key: str = "") -> str:
        state = self._get_state(session_key)
        return "\n".join(state.buffer)

    def _next_step(self, session_key: str) -> int:
        state = self._get_state(session_key)
        step = state.step_counter
        state.step_counter += 1
        return step

    async def show(self, text: str, session_key: str = "", chat_id: str = "",
                   streaming: bool = False, header: str = "") -> None:
        state = self._get_state(session_key)

        # 飞书实时推送（与下方 CLI transcript 镜像可并存）
        if state.feishu_send and state.feishu_chat_id:
            formatted = "\n".join(f"     {line}" for line in text.split("\n"))
            try:
                await state.feishu_send(state.feishu_chat_id, formatted, "gray")
            except Exception as e:
                _logger.warning("飞书思考发送失败: %s", e, exc_info=True)
                err = f"\u26a0\ufe0f \u98de\u4e66\u53d1\u9001\u5931\u8d25: {e}\n"
                if self._output_sink:
                    if self._sink_has_kind:
                        self._output_sink(err, "label")
                    else:
                        self._output_sink(err)

            if not self._output_sink:
                return

        if self._buffer_enabled:
            state.buffer.extend(f"     {line}" for line in text.split("\n"))
            return

        # CLI（全屏 sink 或 print_formatted_text；飞书+sink 时镜像到 transcript）
        if streaming:
            # 首次流式：打印 header 标签
            if state.stream_step is None:
                state.stream_step = self._next_step(session_key)
                state.stream_header = header or ""
                state.stream_printed = 0
                state.stream_first_body_chunk = True
                label = f"  \U0001f4ad [{state.stream_step}] {state.stream_header}"
                self._emit_line(label, "blue")

            # 增量输出：只打印新增的字符
            if state.stream_printed == 0 and text == state.stream_header:
                return  # 纯 header 调用，不打印内容
            new_text = text[state.stream_printed :]
            if new_text:
                new_text = self._indent_stream_body_paragraphs(new_text, state)
                self._emit(new_text)
                state.stream_printed = len(text)
        else:
            # 非流式：结束之前的流式
            if state.stream_step is not None and not state.stream_done:
                self._emit("\n")
                state.stream_done = True
            state.stream_step = None
            state.stream_header = ""
            state.stream_done = False
            state.stream_printed = 0
            state.stream_first_body_chunk = True

            step = self._next_step(session_key)
            lines = (text or "").splitlines() or [""]
            self._emit_line(f"  \U0001f4ad [{step}]", "blue")
            body = "\n".join(f"    {ln}" for ln in lines)
            self._emit(body + "\n")

    def end_thinking(self) -> None:
        """结束当前流式显示块。"""
        for state in self._states.values():
            if state.stream_step is not None and not state.stream_done:
                self._emit("\n")
                state.stream_done = True
                state.stream_step = None
                state.stream_header = ""
                state.stream_printed = 0
                state.stream_first_body_chunk = True
        if self._default.stream_step is not None and not self._default.stream_done:
            self._emit("\n")
            self._default.stream_done = True
            self._default.stream_step = None
            self._default.stream_header = ""
            self._default.stream_printed = 0
            self._default.stream_first_body_chunk = True


__all__ = ["ThinkingDisplay"]
