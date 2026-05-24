"""Engine — 思考过程显示

拆分自 unified.py。

职责：
- 步骤编号（按会话隔离计数器）
- CLI 流式输出（写入 Application 输出缓冲区）
- 飞书会话：通过回调推送思考（与 CLI 终端输出策略不同）
- 多会话并发安全：每个会话独立状态，互不干扰

飞书卡片节流与合并策略见 ``docs/FEISHU.md``、``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text

_logger = logging.getLogger(__name__)


def _merge_tools_enabled() -> bool:
    """同轮工具行与流式思考合并展示；``MINIAGENT_THINKING_MERGE_TOOLS=0`` 关闭。

    合并路径依赖保留 ``stream_header`` 直至新一轮流式或 ``end_thinking``，以便同一轮多次工具连续追加。
    """
    return os.environ.get("MINIAGENT_THINKING_MERGE_TOOLS", "1") != "0"


def _cli_thinking_rich_enabled() -> bool:
    """全屏 CLI 下是否对非流式思考正文尝试 Rich→ANSI（``MINIAGENT_CLI_THINKING_RICH``）。"""
    v = os.environ.get("MINIAGENT_CLI_THINKING_RICH", "").strip().lower()
    return v in ("1", "true", "yes")


def _cli_thinking_render_width() -> int:
    """无注入宽度回调时，用终端列宽推导 Rich 渲染宽度。"""
    try:
        return max(40, shutil.get_terminal_size(fallback=(100, 24)).columns - 4)
    except Exception:
        return 96


def indent_stream_thinking_suffix(full_text: str, prev_printed: int, *, indent: str = "") -> str:
    """为流式思考正文增量加可选「段首」前缀（默认无，顶格输出）。

    依据 **完整累积正文** 判断换段（``\\n\\n`` 后的首行），修正仅对增量 ``split`` 导致
    「段界落在 chunk 边界」时丢失前缀的问题。
    """
    full = (full_text or "").replace("\r\n", "\n")
    n = len(full)
    start = max(0, min(prev_printed, n))
    if start >= n:
        return ""
    if not indent:
        return full[start:n]
    out: list[str] = []
    i = start
    while i < n:
        line_start = i
        j = full.find("\n", i)
        if j == -1:
            segment = full[i:n]
            i = n
        else:
            segment = full[i : j + 1]
            i = j + 1
        if not segment:
            continue
        para_first = line_start == 0 or (
            line_start >= 2 and full[line_start - 2 : line_start] == "\n\n"
        )
        if para_first and segment.strip():
            first_nl = segment.find("\n")
            if first_nl == -1:
                core = segment
                ending = ""
            else:
                core = segment[:first_nl]
                ending = segment[first_nl:]
            if core and not core.startswith(indent):
                segment = indent + core + ending
        out.append(segment)
    return "".join(out)


def _thinking_body_looks_like_markdown(text: str) -> bool:
    """启发式判断正文是否像 Markdown（代码围栏、表格、标题、强调等）。"""
    s = text or ""
    if not s.strip():
        return False
    if "```" in s:
        return True
    if s.count("|") >= 2 and "\n" in s:
        return True
    if re.search(r"^#{1,6}\s", s, re.MULTILINE):
        return True
    if "**" in s or "__" in s:
        return True
    return False


# 飞书发送回调：streaming=True 走 PATCH 节流；False 时 finalize+新卡，或 merge_tools 时追加同卡；
# finalize_only=True 时仅 PATCH 收尾当前流式卡并清空状态，不另发独立卡（阶段切换用）。
OnFeishuSend = Callable[..., Awaitable[None]]


class _SessionThinkingState:
    """单个会话的思考状态（内部使用）。"""

    __slots__ = (
        "step_counter",
        "buffer",
        "feishu_send",
        "feishu_chat_id",
        "feishu_reply_to_message_id",
        "feishu_reply_in_thread",
        "feishu_mirror_cli",
        "stream_step",
        "stream_header",
        "stream_done",
        "stream_printed",
        "feishu_thinking_message_id",
        "feishu_stream_accumulated",
        "feishu_last_patch_monotonic",
        "feishu_last_patched_char_len",
        "feishu_patch_budget",
        "feishu_tool_section_started",
        "turn_number",
    )

    step_counter: int
    buffer: list[str]
    feishu_send: OnFeishuSend | None
    feishu_chat_id: str
    feishu_reply_to_message_id: str | None
    feishu_reply_in_thread: bool
    feishu_mirror_cli: bool
    stream_step: int | None
    stream_header: str
    stream_done: bool
    stream_printed: int  # 已打印的字符数（用于增量输出）
    feishu_thinking_message_id: str | None
    feishu_stream_accumulated: str
    feishu_last_patch_monotonic: float
    feishu_last_patched_char_len: int
    feishu_patch_budget: int
    feishu_tool_section_started: bool
    turn_number: int

    def __init__(self) -> None:
        """初始化会话级流式/飞书 PATCH 状态为默认值。"""
        self.step_counter = 0
        self.buffer = []
        self.feishu_send = None
        self.feishu_chat_id = ""
        self.feishu_reply_to_message_id = None
        self.feishu_reply_in_thread = False
        self.feishu_mirror_cli = True
        self.stream_step = None
        self.stream_header = ""
        self.stream_done = False
        self.stream_printed = 0
        self.feishu_thinking_message_id = None
        self.feishu_stream_accumulated = ""
        self.feishu_last_patch_monotonic = 0.0
        self.feishu_last_patched_char_len = -1
        self.feishu_patch_budget = 0
        self.feishu_tool_section_started = False
        self.turn_number = 0


class ThinkingDisplay:
    """思考过程显示（CLI 终端 + 飞书实时发送）

    CLI：流式输出到终端，原地更新。
    飞书侧会话：通过已注册的回调发送思考内容。
    """

    def __init__(self) -> None:
        """构造显示协调器：按 ``session_key`` 分桶状态，并保留无会话时的默认桶。"""
        self._states: dict[str, _SessionThinkingState] = {}
        self._default: _SessionThinkingState = _SessionThinkingState()
        self._buffer_enabled: bool = False
        # Application 输出缓冲区回调（用于全屏模式）
        self._output_sink: Callable[..., None] | None = None
        self._sink_has_kind: bool = False
        self._sink_accepts_ansi_markdown: bool = False
        # 全屏 TUI 下与 Assistant 回复区 Rich 宽度对齐（见 main.run_cli_loop）
        self._cli_markdown_width_fn: Callable[[], int] | None = None

    def set_cli_markdown_width(self, fn: Callable[[], int] | None) -> None:
        """设置 Rich 思考块渲染宽度；与 ``_cli_block_reply`` 的 ``md_w`` 一致时换行对齐。"""
        self._cli_markdown_width_fn = fn

    def _cli_rich_markdown_width(self) -> int:
        """Rich 渲染宽度：优先回调，其次终端列宽。"""
        if self._cli_markdown_width_fn is not None:
            try:
                return max(40, int(self._cli_markdown_width_fn()))
            except Exception:
                pass
        return _cli_thinking_render_width()

    def set_output_sink(self, sink: Callable[..., None] | None) -> None:
        """设置输出目标（全屏模式写入 transcript，否则 None 走 print）。

        若 sink 接受第二参数 ``kind``（``"label"`` | ``"chunk"``），则用于分区着色。
        若支持关键字参数 ``ansi_markdown``（或 ``**kwargs``），可由 Rich 思考块写入 transcript。
        """
        self._output_sink = sink
        self._sink_has_kind = False
        self._sink_accepts_ansi_markdown = False
        if sink is not None:
            try:
                sig = inspect.signature(sink)
                params = sig.parameters
                self._sink_has_kind = len(params) >= 2
                self._sink_accepts_ansi_markdown = "ansi_markdown" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
            except (TypeError, ValueError):
                self._sink_has_kind = False
                self._sink_accepts_ansi_markdown = False

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

    def _get_state(self, session_key: str) -> _SessionThinkingState:
        """懒创建并返回某会话的思考状态对象。"""
        if session_key not in self._states:
            self._states[session_key] = _SessionThinkingState()
        return self._states[session_key]

    def reset_counter(self, session_key: str = "") -> None:
        """重置指定会话的步骤计数、缓冲与飞书流式字段（新用户轮次前调用）。"""
        state = self._get_state(session_key)
        state.step_counter = 0
        state.buffer.clear()
        state.feishu_send = None
        state.feishu_chat_id = ""
        state.feishu_reply_to_message_id = None
        state.feishu_reply_in_thread = False
        state.feishu_mirror_cli = True
        state.stream_step = None
        state.stream_header = ""
        state.stream_done = False
        state.stream_printed = 0
        state.feishu_thinking_message_id = None
        state.feishu_stream_accumulated = ""
        state.feishu_last_patch_monotonic = 0.0
        state.feishu_last_patched_char_len = -1
        state.feishu_patch_budget = 0
        state.feishu_tool_section_started = False

    def next_turn(self, session_key: str = "") -> int:
        """递增并返回 turn_number（新用户轮次前调用）。"""
        state = self._get_state(session_key)
        state.turn_number += 1
        return state.turn_number

    def thinking_state(self, session_key: str) -> Any:
        """返回会话级思考状态（供引擎 finalize 飞书流式卡片）。"""
        return self._get_state(session_key)

    def enable_feishu(
        self,
        session_key: str,
        chat_id: str,
        send_callback: OnFeishuSend,
        *,
        reply_to_message_id: str | None = None,
        reply_in_thread: bool = False,
        mirror_cli: bool = True,
    ) -> None:
        """为会话注册飞书 chat_id 与发送协程，用于思考卡片 PATCH。

        ``reply_to_message_id`` 非空时，首张思考卡可走「回复消息」API（见 ``MINIAGENT_FEISHU_REPLY_TARGET``）。
        ``mirror_cli=False`` 时全屏 transcript 不重复打印飞书侧思考（见 ``cli_feishu_policy``）。
        """
        state = self._get_state(session_key)
        state.feishu_chat_id = chat_id
        state.feishu_send = send_callback
        state.feishu_reply_to_message_id = (reply_to_message_id or "").strip() or None
        state.feishu_reply_in_thread = bool(reply_in_thread)
        state.feishu_mirror_cli = mirror_cli

    def enable_buffer(self) -> None:
        """打开默认桶缓冲（不落盘终端），用于仅需收集思考文本的场景。"""
        self._buffer_enabled = True
        self._default.buffer.clear()
        self._default.feishu_send = None

    def disable_buffer(self, session_key: str = "") -> None:
        """关闭缓冲并清空指定会话或全局默认桶的飞书句柄。"""
        if session_key:
            state = self._get_state(session_key)
            state.buffer.clear()
            state.feishu_send = None
            state.feishu_chat_id = ""
            state.feishu_reply_to_message_id = None
            state.feishu_reply_in_thread = False
        else:
            self._buffer_enabled = False
            self._default.buffer.clear()
            self._default.feishu_send = None
            self._default.feishu_chat_id = ""
            self._default.feishu_reply_to_message_id = None
            self._default.feishu_reply_in_thread = False

    def get_buffered(self, session_key: str = "") -> str:
        """返回缓冲中的思考行拼接文本（换行连接）。"""
        state = self._get_state(session_key)
        return "\n".join(state.buffer)

    def _next_step(self, session_key: str) -> int:
        """分配并递增会话内步骤序号（💡 [n] 标签用）。"""
        state = self._get_state(session_key)
        step = state.step_counter
        state.step_counter += 1
        return step

    def _should_emit_cli(self, state: _SessionThinkingState) -> bool:
        """无 transcript sink 且仅走飞书时不在本机重复打印；仍依赖下方逻辑更新 stream_header 等状态。"""
        if self._output_sink:
            if state.feishu_send and state.feishu_chat_id and not state.feishu_mirror_cli:
                return False
            return True
        if state.feishu_send and state.feishu_chat_id:
            return False
        return True

    async def show(
        self,
        text: str,
        session_key: str = "",
        chat_id: str = "",
        streaming: bool = False,
        header: str = "",
    ) -> None:
        """展示一段思考：同步飞书卡片、CLI transcript/print、merge_tools 与流式状态机。"""
        state = self._get_state(session_key)

        hdr = (header or "").strip()

        # 流式阶段切换：先于飞书 PATCH，避免新阶段正文拼进上一张卡
        phase_changed = (
            streaming and bool(hdr) and bool(state.stream_header) and hdr != state.stream_header
        )
        if phase_changed:
            if state.feishu_send and state.feishu_chat_id:
                open_feishu = bool(getattr(state, "feishu_thinking_message_id", None))
                if state.stream_step is not None or open_feishu:
                    try:
                        await state.feishu_send(
                            state.feishu_chat_id,
                            "",
                            "gray",
                            is_new_round=False,
                            streaming=False,
                            merge_tools=False,
                            finalize_only=True,
                        )
                    except TypeError:
                        _logger.debug(
                            "feishu_send 不支持 finalize_only，阶段切换时可能未收尾思考卡",
                            exc_info=True,
                        )
            # 注意：飞书状态由后续 push_feishu_thinking_stream(new_round=True) 统一清理，
            # 此处不重复清除，避免与 new_round 路径双重清零。
            # CLI 空行由下方 streaming 块的 stream_done 检查统一处理，避免此处 emit 后又在下方的
            # stream_step=None 分支再次 emit，造成双倍空行。
            state.stream_done = True
            state.stream_step = None
            state.stream_header = ""
            state.stream_printed = 0

        merge_tools = (
            _merge_tools_enabled()
            and not streaming
            and bool(hdr)
            and bool(state.stream_header)
            and hdr == state.stream_header
        )

        # 飞书实时推送（与下方 CLI transcript 镜像可并存）；正文用原始文本便于 lark_md
        if state.feishu_send and state.feishu_chat_id:
            try:
                # 同一步/同一 thinking_header 内工具后继续流式：不新开卡片（merge_tools 后保留 stream_step）
                is_new_round = (
                    streaming
                    and state.stream_step is None
                    and (not state.stream_header or hdr != state.stream_header)
                )
                await state.feishu_send(
                    state.feishu_chat_id,
                    text,
                    "gray",
                    is_new_round=is_new_round,
                    streaming=streaming,
                    merge_tools=merge_tools,
                    finalize_only=False,
                )
            except Exception as e:
                _logger.warning("飞书思考发送失败: %s", e, exc_info=True)
                err = f"\u26a0\ufe0f \u98de\u4e66\u53d1\u9001\u5931\u8d25: {e}\n"
                if self._output_sink:
                    if self._sink_has_kind:
                        self._output_sink(err, "label")
                    else:
                        self._output_sink(err)

        if merge_tools:
            if state.stream_step is not None and not state.stream_done:
                if self._should_emit_cli(state):
                    self._emit("\n")
                state.stream_done = True
            lines = (text or "").splitlines() or [""]
            if self._buffer_enabled:
                state.buffer.extend(lines)
            elif self._should_emit_cli(state):
                body = "\n".join(lines)
                self._emit(body + "\n")
            # 保留 stream_step / stream_printed / stream_header：同一步内工具后继续流式不新开 CLI 标签、不重复打印已输出正文
            state.stream_done = False
            return

        if self._buffer_enabled:
            state.buffer.extend(text.split("\n"))
            return

        # CLI（全屏 sink 或 print_formatted_text；飞书+sink 时镜像到 transcript）
        if streaming:
            # 首次流式或新阶段：打印 header 标签
            if state.stream_step is None:
                # 前一个流式阶段已结束（非流式调用结束或 end_thinking），先补空行
                if state.stream_done:
                    if self._should_emit_cli(state):
                        self._emit("\n\n")  # 阶段间空行：结束上一阶段 + 留一行间隔
                state.stream_step = self._next_step(session_key)
                state.stream_header = header or ""
                state.stream_printed = 0
                state.stream_done = False
                label = f"\U0001f4ad [{state.stream_step}] {state.stream_header}"
                if self._should_emit_cli(state):
                    self._emit_line(label, "blue")

            # 增量输出：只打印新增的字符
            full = text.replace("\r\n", "\n")
            if state.stream_printed == 0 and full == state.stream_header:
                state.stream_printed = -1  # 标记"仅 header"，下次正文调用时重置
                return  # 纯 header 调用，不打印内容
            # 累积假设破裂检测（如 header-only 后 body 重置了正文）
            if state.stream_printed < 0:
                state.stream_printed = 0
            new_text = indent_stream_thinking_suffix(full, state.stream_printed)
            if new_text and self._should_emit_cli(state):
                self._emit(new_text)
            state.stream_printed = len(full)
        else:
            # 非流式：结束之前的流式
            saved_header = ""
            if state.stream_step is not None and not state.stream_done:
                if self._should_emit_cli(state):
                    self._emit("\n\n")  # 阶段间空行：结束流式 + 留一行间隔
                # 飞书侧也需收尾当前流式卡片
                if state.feishu_send and state.feishu_chat_id:
                    try:
                        await state.feishu_send(
                            state.feishu_chat_id,
                            "",
                            "gray",
                            is_new_round=False,
                            streaming=False,
                            merge_tools=False,
                            finalize_only=True,
                        )
                    except TypeError:
                        pass
                state.stream_done = True
                saved_header = state.stream_header
            state.stream_step = None
            state.stream_header = ""
            state.stream_done = False
            state.stream_printed = 0

            step = self._next_step(session_key)
            lines = (text or "").splitlines() or [""]
            if self._should_emit_cli(state):
                hdr_part = f" {saved_header}" if saved_header else ""
                self._emit_line(f"\U0001f4ad [{step}]{hdr_part}", "blue")
                body_md = text or ""
                ansi_body: str | None = None
                if (
                    _cli_thinking_rich_enabled()
                    and self._sink_accepts_ansi_markdown
                    and self._output_sink
                    and _thinking_body_looks_like_markdown(body_md)
                ):
                    from miniagent.engine.markdown_cli import render_markdown_to_ansi

                    ansi_body = render_markdown_to_ansi(
                        body_md, width=self._cli_rich_markdown_width()
                    )
                if (
                    ansi_body
                    and ansi_body.strip()
                    and self._output_sink
                    and self._sink_accepts_ansi_markdown
                ):
                    self._output_sink("", "chunk", ansi_markdown=ansi_body)
                    self._emit("\n")  # 非流式正文后补换行，避免与下一区块黏连
                else:
                    body = "\n".join(lines)
                    self._emit(body + "\n")

    def end_thinking(self) -> None:
        """结束当前流式显示块。"""
        for state in self._states.values():
            if state.stream_step is not None and not state.stream_done:
                if self._should_emit_cli(state):
                    self._emit("\n\n\n")  # 思考结束空行（2行空白）
                state.stream_done = True
                state.stream_step = None
                state.stream_header = ""
                state.stream_printed = 0
        if self._default.stream_step is not None and not self._default.stream_done:
            if self._should_emit_cli(self._default):
                self._emit("\n\n\n")  # 思考结束空行（2行空白）
            self._default.stream_done = True
            self._default.stream_step = None
            self._default.stream_header = ""
            self._default.stream_printed = 0


__all__ = ["ThinkingDisplay", "indent_stream_thinking_suffix"]
