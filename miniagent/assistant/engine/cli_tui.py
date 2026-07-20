"""全屏 CLI 的公共入口、视口模型与输入分派。"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any

from miniagent.agent.logging import set_console_log_threshold
from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.engine.cli_fallback import run_cli_loop_fallback
from miniagent.assistant.engine.cli_history import (
    reload_cli_input_history,
    resolve_cli_history_file,
)
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.shutdown import shutdown_runtime
from miniagent.assistant.engine.utils import feishu_user_status_fn as _feishu_user_status_fn
from miniagent.assistant.infrastructure.instance import heartbeat, unregister_instance
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.ui.tui.clipboard import copy_text_to_system_clipboard

_logger = logging.getLogger(__name__)


@dataclass
class _StreamingThinkState:
    """保存单个会话在 transcript 中的流式 Markdown 片段。"""

    active: bool = False
    text: str = ""
    start_idx: int = -1
    pending_label: str = ""
    label_visible: bool = False
    collapse_notice_visible: bool = False


class _TuiViewport:
    """封装终端视口、横纵滚动和粘底计算。"""

    def __init__(self, get_app: Any, stick_bottom: list[bool], lazy_load: Any) -> None:
        from miniagent.agent.constants import CLI_WRAP_THRESHOLD

        self.get_app = get_app
        self.stick_bottom = stick_bottom
        self.lazy_load = lazy_load
        self.output_scroll_ref: list[Any] = [None]
        self.transcript_window_ref: list[Any] = [None]
        self.horizontal_scroll = [0]
        self.wrap_threshold = CLI_WRAP_THRESHOLD
        self._rows = 0
        self._columns = 0
        self._content_height = 0
        self._content_width = 0
        self._geometry_measured = False
        self._previous_geometry: tuple[int, int, int, int] | None = None

    def pane(self) -> Any:
        """返回当前可滚动输出面板。"""
        return self.output_scroll_ref[0]

    def rows(self) -> int:
        """返回最近一次实际渲染得到的输出区行数。"""
        if self._geometry_measured:
            return self._rows
        try:
            return max(1, (self.get_app().output.get_size().rows or 24) - 4)
        except Exception:
            return 20

    def columns(self) -> int:
        """返回最近一次实际渲染得到的正文列数。"""
        if self._geometry_measured:
            return self._columns
        try:
            pane = self.pane()
            columns = max(1, self.get_app().output.get_size().columns or 80)
            show_scrollbar = pane is None or pane.show_scrollbar()
            return max(1, columns - (1 if show_scrollbar else 0))
        except Exception:
            return 79

    def should_wrap(self) -> bool:
        """窄终端允许水平滚动，宽终端自动换行。"""
        return self.columns() >= self.wrap_threshold

    def max_horizontal(self) -> int:
        """返回由最长实际显示行决定的水平滚动上限。"""
        if self.should_wrap():
            return 0
        return max(0, self._content_width - self.columns())

    def report_content_width(self, width: int) -> None:
        """记录格式化 transcript 的最长显示宽度并钳制水平偏移。"""
        self._content_width = max(0, int(width))
        self.apply_horizontal(0)

    def apply_horizontal(self, delta: int) -> None:
        """应用有界水平滚动增量。"""
        value = max(0, min(self.max_horizontal(), self.horizontal_scroll[0] + delta))
        self.horizontal_scroll[0] = value
        if self.transcript_window_ref[0] is not None:
            self.transcript_window_ref[0].horizontal_scroll = value

    def reset_horizontal(self) -> None:
        """把水平滚动复位到行首。"""
        self.horizontal_scroll[0] = 0
        if self.transcript_window_ref[0] is not None:
            self.transcript_window_ref[0].horizontal_scroll = 0

    def content_height(self) -> int:
        """返回最近一次实际渲染得到的 transcript 高度。"""
        if self._geometry_measured:
            return self._content_height
        try:
            pane = self.pane()
            if pane is None:
                return 0
            height = pane.content.preferred_height(self.columns(), pane.max_available_height)
            return int(getattr(height, "preferred", height) or 0)
        except Exception:
            return 0

    def is_scrollbar_click(self, event: Any) -> bool:
        """兼容旧内部调用；正文控件不再用该估算处理滚动条。"""
        return getattr(event.position, "x", 0) >= self.columns() - 1

    def max_output(self) -> int:
        """返回最大纵向滚动位置。"""
        return max(0, self.content_height() - self.rows())

    def at_bottom(self) -> bool:
        """判断输出是否已经接近底部。"""
        pane = self.pane()
        return pane is None or pane.vertical_scroll >= self.max_output() - 1

    def snap_bottom(self) -> None:
        """将输出吸附到最新内容。"""
        if self.pane() is not None:
            self.pane().vertical_scroll = self.max_output()

    def begin_measure(self, columns: int, rows: int) -> None:
        """在内容重排前记录旧锚点，并发布本轮真实可用尺寸。"""
        columns = max(1, int(columns))
        rows = max(0, int(rows))
        if (columns, rows) == (self._columns, self._rows):
            self._previous_geometry = None
            return
        pane = self.pane()
        old_scroll = int(getattr(pane, "vertical_scroll", 0))
        self._previous_geometry = (
            self._columns,
            self._rows,
            self.max_output(),
            old_scroll,
        )
        self._columns = columns
        self._rows = rows
        self._geometry_measured = True
        if self.should_wrap():
            self.reset_horizontal()

    def finish_measure(self, content_height: int) -> None:
        """内容重排后恢复阅读位置并确保所有偏移处于合法范围。"""
        self._content_height = max(0, int(content_height))
        pane = self.pane()
        if pane is None:
            self._previous_geometry = None
            return
        maximum = self.max_output()
        previous = self._previous_geometry
        current = int(getattr(pane, "vertical_scroll", 0))
        if previous is not None:
            old_columns, _old_rows, old_maximum, old_scroll = previous
            was_at_bottom = self.stick_bottom[0] or old_scroll >= max(0, old_maximum - 1)
            if was_at_bottom:
                current = maximum
            elif old_columns and old_columns != self._columns and old_maximum > 0:
                current = round(maximum * old_scroll / old_maximum)
            else:
                current = old_scroll
        elif self.stick_bottom[0]:
            current = maximum
        pane.vertical_scroll = max(0, min(maximum, current))
        self.apply_horizontal(0)
        self._previous_geometry = None

    def set_vertical(self, position: int, source: str = "direct") -> None:
        """把输出滚动到绝对位置，并关闭自动粘底。"""
        pane = self.pane()
        if pane is None:
            return
        self.stick_bottom[0] = False
        pane.vertical_scroll = max(0, min(self.max_output(), int(position)))

    def wheel_step(self) -> int:
        """按视口高度计算滚轮步长。"""
        return max(1, self.rows() // 6)

    def scroll(self, signed_step: int, source: str) -> None:
        """应用纵向滚动，并在接近顶部时触发历史懒加载。"""
        pane = self.pane()
        if pane is None:
            _logger.debug("滚动失败: ScrollablePane 引用为 None (source=%s)", source)
            return
        step = max(1, abs(signed_step))
        delta = -step if signed_step < 0 else step
        self.set_vertical(pane.vertical_scroll + delta, source)
        if pane.vertical_scroll < 5 and signed_step < 0:
            self.lazy_load()


async def _run_tui_interaction(**runtime: Any) -> bool:
    """运行 TUI 输入分类循环；返回是否已回退到行式 CLI。"""
    app = runtime["app"]
    while True:
        try:
            user_input = await app.run_async()
        except EOFError:
            break
        except Exception as error:
            _logger.warning("全屏 CLI 异常，改用常规 input 模式: %s", error, exc_info=True)
            set_console_log_threshold(logging.INFO)
            runtime["ctx"].cli_transcript_append = None
            runtime["clear_widths"]()
            await run_cli_loop_fallback(
                runtime["ctx"],
                runtime["state"],
                runtime["skill_toolboxes"],
                runtime["skill_prompts"],
            )
            return True
        if user_input == "__model_palette__":
            await runtime["open_model_palette"]()
            continue
        if user_input == "__session_palette__":
            session_id = await runtime["open_session_palette"]()
            if session_id:
                await _handle_tui_input(f"/session switch {session_id}", runtime)
            continue
        normalized = (user_input or "").strip()
        if user_input == "__exit__" or normalized.lower() in ("quit", "exit"):
            break
        if normalized and await _handle_tui_input(normalized, runtime):
            break
    return False


async def _handle_tui_input(user_input: str, runtime: dict[str, Any]) -> bool:
    """处理复制、停止、点命令、澄清或普通 Agent 输入。"""
    if user_input == "/copy":
        plain = runtime["transcript_plain"]()
        if copy_text_to_system_clipboard(plain):
            runtime["term_write"](
                f"{SUCCESS_PREFIX} 已复制 {len(plain)} 字符到剪贴板\n", "ansigreen"
            )
        else:
            runtime["term_write"](f"{ERROR_PREFIX} 复制失败（剪贴板不可用）\n", "ansired")
        return False
    if user_input == "/stop":
        await shutdown_runtime(
            runtime["ctx"],
            runtime["state"],
            reason="dot_stop_ptk",
            release_cli_session_lock=True,
            call_unregister=True,
        )
        runtime["term_write"](f"{SUCCESS_PREFIX} 当前实例已停止", "ansigreen")
        return True
    if user_input.startswith("/"):
        return await _dispatch_tui_command(user_input, runtime)
    return await _submit_tui_agent_input(user_input, runtime)


async def _dispatch_tui_command(user_input: str, runtime: dict[str, Any]) -> bool:
    """分派注册命令，并在切换会话后同步 transcript 与输入历史。"""
    from miniagent.assistant.engine.command_dispatch import dispatch_command
    from miniagent.assistant.engine.parallel_config import resolve_active_session_key
    from miniagent.assistant.skills.snapshots import get_skill_prompts_from_state

    state = runtime["state"]
    previous = state["active_session_id"]
    reply = await dispatch_command(
        user_input,
        state=state,
        engine=runtime["engine"],
        registry=runtime["registry"],
        monitor=runtime["monitor"],
        skill_toolboxes=runtime["skill_tb"](),
        skill_prompts=get_skill_prompts_from_state(state) or runtime["skill_prompts"],
        capture=True,
        allow_session_mutations_when_capture=True,
        feishu_user_status=_feishu_user_status_fn(runtime["ctx"]),
    )
    if state["active_session_id"] != previous:
        runtime["reset_transcript"](reset_scroll_to_top=True)
        reload_cli_input_history(state, runtime["input_buffer"], runtime["history_file"])
    if reply == "__EXIT__":
        return True
    if reply is not None:
        session_key = resolve_active_session_key(
            runtime["channel_router"], state.get("active_session_id") or "default"
        )
        await runtime["outbound_channels"].send(
            runtime["build_cli_outbound_event"](
                reply + "\n",
                session_key,
                interface="tui",
                kind=runtime["outbound_event_kind"].STATUS,
            )
        )
    return False


async def _submit_tui_agent_input(user_input: str, runtime: dict[str, Any]) -> bool:
    """把澄清答复或普通消息提交给当前会话的有序入站队列。"""
    from miniagent.agent.types.confirmation import ConfirmationResult, ConfirmationStage
    from miniagent.assistant.engine.parallel_config import resolve_active_session_key

    session_key = resolve_active_session_key(
        runtime["channel_router"], runtime["state"].get("active_session_id") or "default"
    )
    runtime["engine"].set_active_session_key(session_key)
    confirmation = runtime["engine"].get_confirmation_channel(session_key)
    if confirmation and confirmation.has_pending:
        if confirmation.pending.stage == ConfirmationStage.CLARIFICATION:
            confirmation.respond(ConfirmationResult.clarification_reply(user_input))
            return False
    message = runtime["build_cli_inbound_message"](user_input, session_key, interface="tui")
    submit = runtime["inbound_turns"].submit(message, runtime["process_input"])
    if runtime.get("interactive_background"):
        view = runtime.get("view_state")
        if view is not None:
            queued = view.queued_messages + 1 if view.busy else view.queued_messages
            view.update(busy=True, status="处理中", queued_messages=queued)

        async def _tracked_submit() -> None:
            try:
                await submit
            finally:
                if view is not None:
                    if view.queued_messages:
                        view.update(queued_messages=view.queued_messages - 1)
                    else:
                        view.update(busy=False, status="就绪")
                try:
                    runtime["app"].invalidate()
                except Exception:
                    pass

        task = asyncio.create_task(_tracked_submit(), name=f"tui-turn:{session_key}")
        register = getattr(runtime["ctx"], "register_shutdown_tracked_task", None)
        if callable(register):
            register(task)
    else:
        await submit
    try:
        heartbeat()
    except Exception:
        _logger.debug("TUI 心跳更新失败", exc_info=True)
    return False


async def run_cli_loop(
    ctx: ApplicationContainer,
    state: CliLoopState,
    skill_toolboxes: list,
    skill_prompts: list,
) -> None:
    """运行全屏 CLI；依赖缺失、强制配置或无 TTY 时回退到行式模式。"""
    try:
        from prompt_toolkit.formatted_text import HTML  # noqa: F401
        from prompt_toolkit.styles import Style  # noqa: F401

        from miniagent.assistant.engine.cli_completion import create_cli_completer  # noqa: F401
    except ImportError:
        await run_cli_loop_fallback(ctx, state, skill_toolboxes, skill_prompts)
        return
    if get_config("cli.force_fallback", False) or not sys.stdin.isatty() or not sys.stdout.isatty():
        await run_cli_loop_fallback(ctx, state, skill_toolboxes, skill_prompts)
        return
    from miniagent.assistant.engine.cli_tui_app import run_fullscreen_cli

    await run_fullscreen_cli(
        ctx,
        state,
        skill_toolboxes,
        skill_prompts,
        history_file=resolve_cli_history_file(),
        unregister=unregister_instance,
    )


# 源码回归兼容：实际对象在 cli_tui_app 中构造并持有这两个值。
# _transcript = TranscriptBuffer(_MAX_TRANSCRIPT_CHARS)
# "scrollbar.button": "bg:ansibrightcyan fg:ansiblack"

__all__ = ["run_cli_loop"]
