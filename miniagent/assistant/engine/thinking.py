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
import shutil
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from miniagent.agent.constants import (
    CLI_THINKING_RICH,
    EXECUTION_TERMINAL_WIDTH_CACHE_TTL,
    EXECUTION_THINKING_MERGE_TOOLS,
)
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.engine.thinking_state import (
    OnFeishuSend,
)
from miniagent.assistant.engine.thinking_state import (
    SessionThinkingState as _SessionThinkingState,
)
from miniagent.assistant.engine.thinking_state import (
    clear_feishu_stream_fields as _clear_feishu_stream_fields,
)

# ── 性能优化：缓存终端宽度，避免频繁调用 get_terminal_size ──
_TERMINAL_WIDTH_CACHE_TTL: float = max(0.0, float(EXECUTION_TERMINAL_WIDTH_CACHE_TTL))
_TERMINAL_WIDTH_CACHE: int = 0
_TERMINAL_WIDTH_CACHE_TIME: float = 0.0
_TERMINAL_WIDTH_CACHE_LOCK = threading.Lock()  # 并发安全：保护缓存访问

# prompt_toolkit 是可选依赖（cli extra），未安装时提供 placeholder
try:
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.shortcuts import print_formatted_text

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False
    FormattedText = None  # type: ignore[misc,assignment]
    print_formatted_text = None  # type: ignore[misc,assignment]

_logger = logging.getLogger(__name__)


def _merge_tools_enabled() -> bool:
    """同轮工具行与流式思考合并展示。

    合并路径依赖保留 ``stream_header`` 直至新一轮流式或 ``end_thinking``，以便同一轮多次工具连续追加。
    """
    return EXECUTION_THINKING_MERGE_TOOLS


def _cli_thinking_rich_enabled() -> bool:
    """全屏 CLI 下是否对非流式思考正文尝试 Rich→ANSI。

    优先级：``MINIAGENT_CLI_THINKING_RICH`` 环境变量 > ``cli.thinking_rich`` 配置 >
    Internal 默认值 ``CLI_THINKING_RICH``。
    """
    from miniagent.assistant.infrastructure.env_parse import env_flag
    from miniagent.assistant.infrastructure.json_config import get_config

    default = bool(get_config("cli.thinking_rich", CLI_THINKING_RICH))
    return env_flag("MINIAGENT_CLI_THINKING_RICH", default=default)


def _cli_thinking_render_width() -> int:
    """无注入宽度回调时，用终端列宽推导 Rich 渲染宽度（带 TTL 缓存）。

    并发安全：使用锁保护缓存访问，避免多线程竞争。
    """
    global _TERMINAL_WIDTH_CACHE, _TERMINAL_WIDTH_CACHE_TIME
    with _TERMINAL_WIDTH_CACHE_LOCK:
        now = time.time()
        if (
            _TERMINAL_WIDTH_CACHE > 0
            and (now - _TERMINAL_WIDTH_CACHE_TIME) < _TERMINAL_WIDTH_CACHE_TTL
        ):
            return _TERMINAL_WIDTH_CACHE
        try:
            terminal_width = shutil.get_terminal_size(fallback=(80, 24)).columns
            # 最大500适应宽屏显示器，确保表格完整显示
            width = max(40, min(500, terminal_width - 4))
            _TERMINAL_WIDTH_CACHE = width
            _TERMINAL_WIDTH_CACHE_TIME = now
            return width
        except Exception:
            return 76  # 80 - 4


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


def _cli_thinking_use_rich_render(text: str) -> bool:
    """非空思考正文是否走 Rich→ANSI 渲染（含纯文本，以保证换行宽度一致）。"""
    return bool((text or "").strip())


class ThinkingDisplay:
    """思考过程显示（CLI 终端 + 飞书实时发送）

    CLI：流式输出到终端，原地更新。
    飞书侧会话：通过已注册的回调发送思考内容。
    """

    def __init__(self) -> None:
        """构造显示协调器：按 ``session_key`` 分桶状态（含空字符串默认键）。

        性能优化：Session 状态 LRU 驱逐，防止无限累积（最大 50 个会话状态）。
        """
        self._states: OrderedDict[str, _SessionThinkingState] = OrderedDict()
        self._buffer_enabled: bool = False
        # 性能优化：Session状态最大数量（防止内存泄漏）
        self._max_session_states: int = 50
        # Application 输出缓冲区回调（用于全屏模式）
        self._output_sink: Callable[..., None] | None = None
        self._sink_has_kind: bool = False
        self._sink_accepts_ansi_markdown: bool = False
        self._sink_accepts_session_key: bool = False
        # 全屏 TUI 下与 Assistant 回复区 Rich 宽度对齐（见 cli_tui.run_cli_loop）
        self._cli_markdown_width_fn: Callable[[], int] | None = None
        self._cli_display_lock = threading.Lock()

    def set_cli_markdown_width(self, fn: Callable[[], int] | None) -> None:
        """设置 Rich 思考块渲染宽度；与 ``_cli_block_reply`` 的 ``md_w`` 一致时换行对齐。"""
        self._cli_markdown_width_fn = fn

    def _cli_rich_markdown_width(self) -> int:
        """Rich 渲染宽度：优先回调，其次终端列宽。"""
        if self._cli_markdown_width_fn is not None:
            try:
                return max(40, int(self._cli_markdown_width_fn()))
            except Exception as e:
                _logger.debug("获取markdown宽度失败: %s", e)
        return _cli_thinking_render_width()

    def set_output_sink(self, sink: Callable[..., None] | None) -> None:
        """设置输出目标（全屏模式写入 transcript，否则 None 走 print）。

        若 sink 接受第二参数 ``kind``（``"label"`` | ``"chunk"``），则用于分区着色。
        若支持关键字参数 ``ansi_markdown``（或 ``**kwargs``），可由 Rich 思考块写入 transcript。
        """
        self._output_sink = sink
        self._sink_has_kind = False
        self._sink_accepts_ansi_markdown = False
        self._sink_accepts_session_key = False
        if sink is not None:
            try:
                sig = inspect.signature(sink)
                params = sig.parameters
                self._sink_has_kind = len(params) >= 2
                self._sink_accepts_ansi_markdown = "ansi_markdown" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
                self._sink_accepts_session_key = "session_key" in params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
            except (TypeError, ValueError):
                self._sink_has_kind = False
                self._sink_accepts_ansi_markdown = False
                self._sink_accepts_session_key = False

    def _call_sink(
        self,
        text: str,
        *,
        kind: str | None = None,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        """调用 output_sink，按 sink 签名传递 kind / session_key / ansi_markdown。"""
        if not self._output_sink:
            return
        kwargs: dict[str, Any] = {}
        if self._sink_accepts_session_key:
            kwargs["session_key"] = session_key
        if ansi_markdown is not None and self._sink_accepts_ansi_markdown:
            kwargs["ansi_markdown"] = ansi_markdown
        if kind is not None and self._sink_has_kind:
            if kwargs:
                self._output_sink(text, kind, **kwargs)
            else:
                self._output_sink(text, kind)
        elif kwargs:
            self._output_sink(text, **kwargs)
        else:
            self._output_sink(text)

    def _emit(self, text: str, color: str = "gray", *, session_key: str = "") -> None:
        """统一输出入口（CLI display lock 防止多 session mirror 交错）。

        无 ``output_sink`` 时依赖 ``prompt_toolkit``（cli extra）；未安装则回退纯文本 stdout。
        """
        with self._cli_display_lock:
            if self._output_sink:
                self._call_sink(text, kind="chunk", session_key=session_key)
            elif not _HAS_PROMPT_TOOLKIT:
                sys.stdout.write(text)
                sys.stdout.flush()
            else:
                ft = FormattedText([(f"ansi{color}", text)])
                print_formatted_text(ft, end="")
                sys.stdout.flush()

    def _emit_line(self, text: str, color: str = "gray", *, session_key: str = "") -> None:
        """统一换行输出入口（无 prompt_toolkit 时回退纯文本 stdout）。"""
        with self._cli_display_lock:
            if self._output_sink:
                self._call_sink(text + "\n", kind="label", session_key=session_key)
            elif not _HAS_PROMPT_TOOLKIT:
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
            else:
                ft = FormattedText([(f"ansi{color}", text + "\n")])
                print_formatted_text(ft)
                sys.stdout.flush()

    def _get_state(self, session_key: str) -> _SessionThinkingState:
        """懒创建并返回某会话的思考状态；访问时刷新 LRU 顺序。"""
        if session_key in self._states:
            self._states.move_to_end(session_key)
            return self._states[session_key]
        self._states[session_key] = _SessionThinkingState()
        while len(self._states) > self._max_session_states:
            self._states.popitem(last=False)
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
        _clear_feishu_stream_fields(state)
        state._last_stream_full = ""

    def next_turn(self, session_key: str = "") -> int:
        """递增并返回 turn_number（新用户轮次前调用）。"""
        state = self._get_state(session_key)
        state.turn_number += 1
        return state.turn_number

    def thinking_state(self, session_key: str) -> _SessionThinkingState:
        """返回会话级思考状态（供引擎 finalize 飞书流式卡片）。

        仅供 ``poll_server`` / Engine 读写飞书 PATCH 字段；业务层勿直接改 CLI 流式字段。
        """
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
        state.feishu_session_key = session_key
        state.feishu_send = send_callback
        state.feishu_reply_to_message_id = (reply_to_message_id or "").strip() or None
        state.feishu_reply_in_thread = bool(reply_in_thread)
        state.feishu_mirror_cli = mirror_cli

    def enable_buffer(self, session_key: str = "") -> None:
        """打开缓冲模式：``show`` 仅写入 ``session_key`` 对应 bucket，不输出终端/飞书。

        测试或仅需收集思考文本时使用；生产 Engine 路径默认不走缓冲。
        """
        self._buffer_enabled = True
        state = self._get_state(session_key)
        state.buffer.clear()
        state.feishu_send = None

    def disable_buffer(self, session_key: str = "") -> None:
        """关闭缓冲并清空指定会话（或全部会话）的缓冲与飞书句柄。"""
        if session_key:
            state = self._get_state(session_key)
            state.buffer.clear()
            state.feishu_send = None
            state.feishu_chat_id = ""
            state.feishu_reply_to_message_id = None
            state.feishu_reply_in_thread = False
        else:
            self._buffer_enabled = False
            for state in self._states.values():
                state.buffer.clear()
                state.feishu_send = None
                state.feishu_chat_id = ""
                state.feishu_reply_to_message_id = None
                state.feishu_reply_in_thread = False

    def get_buffered(self, session_key: str = "") -> str:
        """返回 ``session_key`` 缓冲中的思考行（换行连接）。需先 ``enable_buffer``。"""
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

    def _show_streaming(
        self, state: _SessionThinkingState, text: str, *, session_key: str = ""
    ) -> None:
        """流式思考正文增量输出（带内容连续性校验，防止丢字）。"""
        full = text.replace("\r\n", "\n")

        # 首次流式纯 header 标注（如 "[执行]"），不打印正文，标记等待正文
        if state.stream_printed == 0 and full == state.stream_header:
            state.stream_printed = -1  # 标记"仅 header"，下次正文调用时重置
            return

        # 累积假设破裂检测：之前仅收到 header，现在来了带正文的内容
        if state.stream_printed < 0:
            state.stream_printed = 0

        # ── 关键修复：内容连续性校验 ──
        # stream_printed 记录的是上次调用时 full 的字符数。如果本次 full 的
        # 前 stream_printed 个字符与上次不一致（如 LLM 分块累积文本在两次调用间
        # 不是简单的"前缀增长"关系），直接按字符偏移会丢内容。
        # 解决：验证上次"已打印"的文本前缀是否仍是本次 full 的前缀。
        if state.stream_printed > 0 and len(full) > state.stream_printed:
            last_full = state._last_stream_full
            if last_full and not full.startswith(last_full):
                # 内容不连续（如 "[执行] 开始" → LLM 实际正文），重置偏移
                state.stream_printed = 0

        new_text = indent_stream_thinking_suffix(full, state.stream_printed)
        if new_text and self._should_emit_cli(state):
            self._emit(new_text, session_key=session_key)
        state.stream_printed = len(full)
        state._last_stream_full = full  # 保存本次全文供下次校验

    async def _reset_stream_phase(
        self, state: _SessionThinkingState, header: str, reset: bool
    ) -> None:
        """在阶段切换时先收尾飞书卡片，再清理本地流状态。"""
        phase_changed = bool(header and state.stream_header and header != state.stream_header)
        if phase_changed and state.feishu_send and state.feishu_chat_id:
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
        if phase_changed or reset:
            state.stream_done = True
            state.stream_step = None
            state.stream_header = ""
            state.stream_printed = 0
            state._last_stream_full = ""

    async def _push_feishu_update(
        self,
        state: _SessionThinkingState,
        text: str,
        header: str,
        *,
        streaming: bool,
        merge_tools: bool,
        is_last_step: bool,
        session_key: str,
    ) -> None:
        """按流阶段策略投递飞书思考更新，并将失败映射到 CLI 警告。"""
        if not state.feishu_send or not state.feishu_chat_id or (is_last_step and streaming):
            return
        skip_initial = (
            _merge_tools_enabled()
            and not streaming
            and bool(header)
            and state.stream_step is None
            and not state.stream_header
        )
        if skip_initial:
            return
        is_new_round = bool(
            streaming
            and state.stream_step is None
            and (not state.stream_header or header != state.stream_header)
        )
        try:
            await state.feishu_send(
                state.feishu_chat_id,
                text,
                "gray",
                is_new_round=is_new_round,
                streaming=streaming,
                merge_tools=merge_tools,
                finalize_only=False,
            )
        except Exception as error:
            _logger.warning("飞书思考发送失败: %s", error)
            if self._output_sink:
                self._call_sink(
                    f"{WARNING_PREFIX} 飞书发送失败: {error}\n",
                    kind="label",
                    session_key=session_key,
                )

    def _render_body(self, body: str, *, session_key: str) -> None:
        """使用 Rich 渲染正文；不可用时保持纯文本输出。"""
        ansi_body: str | None = None
        if (
            _cli_thinking_rich_enabled()
            and self._sink_accepts_ansi_markdown
            and self._output_sink
            and _cli_thinking_use_rich_render(body)
        ):
            from miniagent.assistant.engine.markdown_cli import render_markdown_to_ansi

            ansi_body = render_markdown_to_ansi(body, width=self._cli_rich_markdown_width())
        if ansi_body and ansi_body.strip() and self._output_sink and self._sink_accepts_ansi_markdown:
            self._call_sink("", kind="chunk", session_key=session_key, ansi_markdown=ansi_body)
            self._emit("\n", session_key=session_key)
        else:
            self._emit(body + "\n", session_key=session_key)

    def _show_merged_tools(
        self, state: _SessionThinkingState, text: str, *, session_key: str
    ) -> None:
        """把同阶段工具行追加到当前思考块。"""
        if state.stream_step is not None and not state.stream_done:
            if self._should_emit_cli(state):
                self._emit("\n", session_key=session_key)
            state.stream_done = True
        lines = (text or "").splitlines() or [""]
        if self._buffer_enabled:
            state.buffer.extend(lines)
        elif self._should_emit_cli(state):
            self._render_body("\n".join(lines), session_key=session_key)
        state.stream_done = False

    def _show_stream_event(
        self,
        state: _SessionThinkingState,
        text: str,
        header: str,
        *,
        is_last_step: bool,
        session_key: str,
    ) -> None:
        """显示一个流式思考增量。"""
        if is_last_step:
            return
        if state.stream_step is None:
            if state.stream_done and self._should_emit_cli(state):
                self._emit("\n\n", session_key=session_key)
            state.stream_step = self._next_step(session_key)
            state.stream_header = header
            state.stream_printed = 0
            state.stream_done = False
            state._last_stream_full = ""
            if self._should_emit_cli(state):
                self._emit_line(
                    f"\U0001f4ad [{state.stream_step}] {state.stream_header}",
                    "gray",
                    session_key=session_key,
                )
        self._show_streaming(state, text, session_key=session_key)

    async def _finish_active_stream(
        self, state: _SessionThinkingState, *, session_key: str
    ) -> str:
        """收尾活动流并返回其阶段标签。"""
        if state.stream_step is None or state.stream_done:
            return ""
        if self._should_emit_cli(state):
            self._emit("\n\n", session_key=session_key)
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
            except TypeError as error:
                _logger.debug("流合并参数不匹配: %s", error)
        state.stream_done = True
        return state.stream_header

    async def _show_non_stream_event(
        self,
        state: _SessionThinkingState,
        text: str,
        header: str,
        *,
        is_last_step: bool,
        session_key: str,
    ) -> None:
        """显示工具通知等非流式思考事件。"""
        if is_last_step and text.strip().endswith("开始"):
            return
        saved_header = await self._finish_active_stream(state, session_key=session_key)
        step = self._next_step(session_key)
        lines = (text or "").splitlines() or [""]
        initialize_merge = bool(
            _merge_tools_enabled()
            and header
            and (state.stream_step is None or state.stream_done)
        )
        if initialize_merge:
            is_transition = bool(state.stream_done)
            state.stream_step = step
            state.stream_header = header
            state.stream_printed = 0
            state.stream_done = False
            state._last_stream_full = ""
            state.feishu_pending_header = header
            if self._should_emit_cli(state):
                if is_transition:
                    self._emit("\n\n", session_key=session_key)
                self._emit_line(
                    f"\U0001f4ad [{state.stream_step}] {state.stream_header}",
                    "gray",
                    session_key=session_key,
                )
                self._render_body("\n".join(lines), session_key=session_key)
            state.stream_done = False
            return
        if self._should_emit_cli(state):
            header_part = f" {saved_header}" if saved_header else ""
            self._emit_line(f"\U0001f4ad [{step}]{header_part}", "gray", session_key=session_key)
            self._render_body(text or "", session_key=session_key)

    async def show(
        self,
        text: str,
        session_key: str = "",
        streaming: bool = False,
        header: str = "",
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None:
        """展示一段思考：同步飞书卡片、CLI transcript/print、merge_tools 与流式状态机。

        状态机流转（按 session_key 隔离）：
        ┌─────────────────────────────────────────────────────────────────┐
        │  stream_step=None, stream_done=False (初始/已收尾)             │
        │         ↓ streaming=True + 首次 text                           │
        │  stream_step=N, stream_header=header, stream_done=False       │
        │         ↓ streaming=True + 同 header                           │
        │  增量输出（_show_streaming），飞书 PATCH 节流                   │
        │         ↓ streaming=False OR header 变化                      │
        │  stream_done=True（结束流式，补空行）                           │
        │         ↓ 再次 streaming=True + 新 header                      │
        │  stream_step=N+1（新阶段，重新计数）                           │
        └─────────────────────────────────────────────────────────────────┘

        阶段切换（header 变化）或 reset=True 时先 PATCH 收尾当前流式卡片，再开新阶段。

        Args:
            text: 思考正文（Markdown 或纯文本）。
            session_key: 会话标识（用于状态隔离）。
            streaming: 是否流式输出（LLM 逐 token）。
            header: 阶段标签（如 ``[规划]``, ``[执行]``, ``[步骤 1/3]``）。
            reset: 是否重置流式状态（用于语义不同的新阶段清除旧状态）。
            is_last_step: 是否为规划的最后一步（最后一步的 LLM 正文不在思考区显示，避免重复）。
        """
        state = self._get_state(session_key)
        normalized_header = (header or "").strip()
        await self._reset_stream_phase(state, normalized_header, reset)
        merge_tools = bool(
            _merge_tools_enabled()
            and not streaming
            and normalized_header
            and state.stream_header
            and normalized_header == state.stream_header
        )
        await self._push_feishu_update(
            state,
            text,
            normalized_header,
            streaming=streaming,
            merge_tools=merge_tools,
            is_last_step=is_last_step,
            session_key=session_key,
        )
        if merge_tools:
            self._show_merged_tools(state, text, session_key=session_key)
            return
        if self._buffer_enabled:
            state.buffer.extend(text.split("\n"))
            return
        if streaming:
            self._show_stream_event(
                state,
                text,
                header or "",
                is_last_step=is_last_step,
                session_key=session_key,
            )
            return
        await self._show_non_stream_event(
            state,
            text,
            normalized_header,
            is_last_step=is_last_step,
            session_key=session_key,
        )

    def end_thinking(self, session_key: str | None = None) -> None:
        """结束流式显示块。

        ``session_key`` 指定时仅收尾该会话；``None`` 时收尾全部。
        """
        if session_key is not None:
            sk = (session_key or "").strip()
            target_keys = [sk] if sk in self._states else []
        else:
            target_keys = list(self._states.keys())

        def _finalize(sk: str, state: _SessionThinkingState) -> None:
            if state.stream_step is not None and not state.stream_done:
                if self._should_emit_cli(state):
                    self._emit("\n\n\n", session_key=sk)
                state.stream_done = True
                state.stream_step = None
                state.stream_header = ""
                state.stream_printed = 0
                state._last_stream_full = ""

        for sk in target_keys:
            _finalize(sk, self._states[sk])


__all__ = ["ThinkingDisplay", "OnFeishuSend", "indent_stream_thinking_suffix"]
