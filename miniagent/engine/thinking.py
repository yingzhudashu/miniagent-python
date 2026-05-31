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
import time
from collections.abc import Awaitable, Callable

# ── 性能优化：缓存终端宽度，避免频繁调用 get_terminal_size ──
_TERMINAL_WIDTH_CACHE: int = 0
_TERMINAL_WIDTH_CACHE_TIME: float = 0.0
_TERMINAL_WIDTH_CACHE_TTL: float = 2.0  # 缓存有效期 2 秒

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
    """同轮工具行与流式思考合并展示；``MINIAGENT_THINKING_MERGE_TOOLS=0`` 关闭。

    合并路径依赖保留 ``stream_header`` 直至新一轮流式或 ``end_thinking``，以便同一轮多次工具连续追加。
    """
    return os.environ.get("MINIAGENT_THINKING_MERGE_TOOLS", "1") != "0"


def _cli_thinking_rich_enabled() -> bool:
    """全屏 CLI 下是否对非流式思考正文尝试 Rich→ANSI（``MINIAGENT_CLI_THINKING_RICH=0`` 关闭）。"""
    v = os.environ.get("MINIAGENT_CLI_THINKING_RICH", "").strip().lower()
    return v not in ("0", "false", "no")


def _cli_thinking_render_width() -> int:
    """无注入宽度回调时，用终端列宽推导 Rich 渲染宽度（带 TTL 缓存）。"""
    global _TERMINAL_WIDTH_CACHE, _TERMINAL_WIDTH_CACHE_TIME
    now = time.time()
    if _TERMINAL_WIDTH_CACHE > 0 and (now - _TERMINAL_WIDTH_CACHE_TIME) < _TERMINAL_WIDTH_CACHE_TTL:
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


def _thinking_body_looks_like_markdown(text: str) -> bool:
    """启发式判断正文是否像 Markdown（代码围栏、表格、标题、强调等）。

    注意：为确保显示宽度一致，普通文本也应通过 Rich 渲染以获得正确的换行处理。
    仅在文本为空时返回 False。
    """
    s = text or ""
    if not s.strip():
        return False
    # 移除旧的条件判断，始终返回 True 以确保宽度一致性
    # Rich Markdown 会正确处理普通文本的换行
    return True


# 飞书发送回调：streaming=True 走 PATCH 节流；False 时 finalize+新卡，或 merge_tools 时追加同卡；
# finalize_only=True 时仅 PATCH 收尾当前流式卡并清空状态，不另发独立卡（阶段切换用）。
OnFeishuSend = Callable[..., Awaitable[None]]


class _SessionThinkingState:
    """单个会话的思考状态（内部使用）。

    飞书 PATCH 节流机制（防止高频 PATCH 请求）：
    ───────────────────────────────────────────────────────────────────
    飞书流式思考卡片通过 PATCH 更新正文，但飞书 API 有频率限制。
    本类通过以下字段实现节流：

    - feishu_last_patch_monotonic: 上次 PATCH 的单调时间（秒）。
    - feishu_last_patched_char_len: 上次 PATCH 时已发送的字符数。
    - feishu_patch_budget: 剩余 PATCH 次数（初始化为 N，每次 PATCH 减 1）。

    节流策略（见 poll_server.py 中的发送逻辑）：
    1. 首次流式创建卡片（POST），获得 message_id。
    2. 后续流式正文：检查距上次 PATCH 是否超过最小间隔（如 0.5s）。
    3. 若超过间隔且 patch_budget > 0：发送 PATCH 更新正文。
    4. 流式结束时 PATCH 收尾（finalize_only=True）。

    字段说明：
    - feishu_stream_accumulated: 累积的流式正文（用于 PATCH 正文）。
    - feishu_stream_llm_len: LLM 正文长度（不含工具段前缀）。
    - feishu_tool_section_started: 是否已进入工具段（影响正文拼接）。
    - feishu_pending_tool_lines: 待发送的工具行（批量 PATCH 时合并）。
    - feishu_pending_header: 待发送的阶段标签（如 ``[步骤 1/3]``）。
    ───────────────────────────────────────────────────────────────────
    """

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
        "feishu_stream_llm_len",
        "feishu_last_patch_monotonic",
        "feishu_last_patched_char_len",
        "feishu_patch_budget",
        "feishu_tool_section_started",
        "feishu_pending_tool_lines",
        "feishu_pending_header",
        "turn_number",
        "_last_stream_full",
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
    feishu_stream_llm_len: int  # LLM 正文字符数，用于工具段保留时的前缀计算
    feishu_last_patch_monotonic: float
    feishu_last_patched_char_len: int
    feishu_patch_budget: int
    feishu_tool_section_started: bool
    feishu_pending_tool_lines: list[str]
    feishu_pending_header: str
    turn_number: int
    _last_stream_full: str

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
        self.feishu_stream_llm_len = 0
        self.feishu_last_patch_monotonic = 0.0
        self.feishu_last_patched_char_len = -1
        self.feishu_patch_budget = 0
        self.feishu_tool_section_started = False
        self.feishu_pending_tool_lines: list[str] = []
        self.feishu_pending_header = ""
        self.turn_number = 0
        self._last_stream_full = ""


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
        state.feishu_pending_tool_lines = []
        state.feishu_pending_header = ""
        state._last_stream_full = ""

    def next_turn(self, session_key: str = "") -> int:
        """递增并返回 turn_number（新用户轮次前调用）。"""
        state = self._get_state(session_key)
        state.turn_number += 1
        return state.turn_number

    def thinking_state(self, session_key: str) -> _SessionThinkingState:
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

    def _show_streaming(self, state: _SessionThinkingState, text: str) -> None:
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
            self._emit(new_text)
        state.stream_printed = len(full)
        state._last_stream_full = full  # 保存本次全文供下次校验

    async def show(
        self,
        text: str,
        session_key: str = "",
        streaming: bool = False,
        header: str = "",
        reset: bool = False,
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
        """
        state = self._get_state(session_key)

        hdr = (header or "").strip()

        # reset=True 或流式/非流式阶段切换：先于飞书 PATCH，避免新阶段正文拼进上一张卡。
        # 注意：不要求 streaming=True，否则 [执行] 开始（streaming=False）无法检测到从规划到执行的 header 变化。
        phase_changed = (
            bool(hdr) and bool(state.stream_header) and hdr != state.stream_header
        )
        # 关键修复：reset=True 也触发状态重置，避免语义不同的新内容与旧流式状态拼接导致重复显示
        should_reset_stream = phase_changed or reset
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
        # 关键修复：流式状态重置在 phase_changed 或 reset=True 时都执行
        # reset=True 表示语义不同的新内容，需要清除旧的 stream_printed 和 _last_stream_full
        if should_reset_stream:
            # 注意：飞书状态由后续 push_feishu_thinking_stream(new_round=True) 统一清理，
            # 此处不重复清除，避免与 new_round 路径双重清零。
            # CLI 空行由下方 streaming 块的 stream_done 检查统一处理，避免此处 emit 后又在下方的
            # stream_step=None 分支再次 emit，造成双倍空行。
            state.stream_done = True
            state.stream_step = None
            state.stream_header = ""
            state.stream_printed = 0
            state._last_stream_full = ""

        merge_tools = (
            _merge_tools_enabled()
            and not streaming
            and bool(hdr)
            and bool(state.stream_header)
            and hdr == state.stream_header
        )

        # 飞书实时推送（与下方 CLI transcript 镜像可并存）；正文用原始文本便于 lark_md
        if state.feishu_send and state.feishu_chat_id:
            # 非流式、无活跃流、merge_tools 开启：仅初始化状态，不发独立卡片。
            # 首张卡片由后续 LLM 流式创建（push_feishu_thinking_stream），工具行
            # 走 append_feishu_thinking_same_card 追加入同卡。
            _skip_feishu_init = (
                _merge_tools_enabled()
                and not streaming
                and bool(hdr)
                and state.stream_step is None
                and not state.stream_header
            )
            if not _skip_feishu_init:
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
                    _logger.warning("飞书思考发送失败: %s", e)
                    err = f"⚠️ 飞书发送失败: {e}\n"
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
                # 与 merge_tools 初始化路径保持一致的 Rich Markdown 渲染
                body_md = "\n".join(lines)
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
                    self._emit("\n")
                else:
                    self._emit(body_md + "\n")
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
                state._last_stream_full = ""
                label = f"\U0001f4ad [{state.stream_step}] {state.stream_header}"
                if self._should_emit_cli(state):
                    self._emit_line(label, "blue")

            self._show_streaming(state, text)
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

            step = self._next_step(session_key)
            lines = (text or "").splitlines() or [""]

            # 无活跃流（或旧流已收尾）但有 header 且 merge_tools 开启：初始化流状态，
            # 使后续同 header 的非流式/流式调用能走 merge_tools 路径合并。
            if (
                _merge_tools_enabled()
                and bool(hdr)
                and (state.stream_step is None or state.stream_done)
            ):
                _is_transition = bool(state.stream_done)
                state.stream_step = step
                state.stream_header = hdr
                state.stream_printed = 0
                state.stream_done = False
                state._last_stream_full = ""
                # 统一存入 feishu_pending_header，供 push_feishu_thinking_stream(new_round=False) 消费
                state.feishu_pending_header = hdr
                if self._should_emit_cli(state):
                    if _is_transition:
                        self._emit("\n\n")  # 阶段间空行：规划与执行之间留一行间隔
                    label = f"\U0001f4ad [{state.stream_step}] {state.stream_header}"
                    self._emit_line(label, "blue")
                if self._should_emit_cli(state):
                    # 与非 merge_tools 路径保持一致的 Rich Markdown 渲染
                    body_md = "\n".join(lines)
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
                        self._emit("\n")
                    else:
                        self._emit(body_md + "\n")
                state.stream_done = False
                return

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
                state._last_stream_full = ""
        if self._default.stream_step is not None and not self._default.stream_done:
            if self._should_emit_cli(self._default):
                self._emit("\n\n\n")  # 思考结束空行（2行空白）
            self._default.stream_done = True
            self._default.stream_step = None
            self._default.stream_header = ""
            self._default.stream_printed = 0
            self._default._last_stream_full = ""


__all__ = ["ThinkingDisplay", "indent_stream_thinking_suffix"]
