"""全屏 CLI 的公共入口、视口模型与输入分派。"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.engine.cli_fallback import run_cli_loop_fallback
from miniagent.engine.cli_history import reload_cli_input_history, resolve_cli_history_file
from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.clipboard import copy_text_to_system_clipboard
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.engine.utils import feishu_user_status_fn as _feishu_user_status_fn
from miniagent.infrastructure.instance import heartbeat, unregister_instance
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import set_console_log_threshold
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX

_logger = logging.getLogger(__name__)


@dataclass
class _StreamingThinkState:
    """保存单个会话在 transcript 中的流式 Markdown 片段。"""

    active: bool = False
    text: str = ""
    start_idx: int = -1


class _TuiViewport:
    """封装终端视口、横纵滚动和粘底计算。"""

    def __init__(self, get_app: Any, stick_bottom: list[bool], lazy_load: Any) -> None:
        from miniagent.core.constants import CLI_WRAP_THRESHOLD

        self.get_app = get_app
        self.stick_bottom = stick_bottom
        self.lazy_load = lazy_load
        self.output_scroll_ref: list[Any] = [None]
        self.transcript_window_ref: list[Any] = [None]
        self.horizontal_scroll = [0]
        self.wrap_threshold = CLI_WRAP_THRESHOLD

    def pane(self) -> Any:
        """返回当前可滚动输出面板。"""
        return self.output_scroll_ref[0]

    def rows(self) -> int:
        """返回扣除输入区后的保守可见行数。"""
        try:
            return max(6, (self.get_app().output.get_size().rows or 24) - 4)
        except Exception:
            return 20

    def columns(self) -> int:
        """返回扣除滚动条后的可用列数。"""
        try:
            pane = self.pane()
            if pane is None:
                return 79
            columns = max(40, self.get_app().output.get_size().columns or 80)
            return max(1, columns - (1 if pane.show_scrollbar() else 0))
        except Exception:
            return 79

    def should_wrap(self) -> bool:
        """窄终端允许水平滚动，宽终端自动换行。"""
        return self.columns() >= self.wrap_threshold

    def max_horizontal(self) -> int:
        """返回有界水平滚动上限。"""
        return max(0, self.columns() * 2)

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

    def is_scrollbar_click(self, event: Any) -> bool:
        """判断鼠标事件是否落在右侧滚动条。"""
        return getattr(event.position, "x", 0) >= self.columns() - 2

    def content_height(self) -> int:
        """返回当前 transcript 的渲染高度。"""
        try:
            pane = self.pane()
            if pane is None:
                return 0
            height = pane.content.preferred_height(self.columns(), pane.max_available_height)
            return int(getattr(height, "preferred", height) or 0)
        except Exception:
            return 0

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

    def wheel_step(self) -> int:
        """按视口高度计算滚轮步长。"""
        return max(1, self.rows() // 6)

    def scroll(self, signed_step: int, source: str) -> None:
        """应用纵向滚动，并在接近顶部时触发历史懒加载。"""
        pane = self.pane()
        if pane is None:
            _logger.debug("滚动失败: ScrollablePane 引用为 None (source=%s)", source)
            return
        self.stick_bottom[0] = False
        step = max(1, abs(signed_step))
        if signed_step < 0:
            pane.vertical_scroll = max(0, pane.vertical_scroll - step)
        else:
            pane.vertical_scroll = min(self.max_output(), pane.vertical_scroll + step)
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
    if user_input.startswith("/") and await _dispatch_tui_command(user_input, runtime):
        return True
    return await _submit_tui_agent_input(user_input, runtime)


async def _dispatch_tui_command(user_input: str, runtime: dict[str, Any]) -> bool:
    """分派注册命令，并在切换会话后同步 transcript 与输入历史。"""
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.engine.parallel_config import resolve_active_session_key
    from miniagent.skills.snapshots import get_skill_prompts_from_state

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
    from miniagent.engine.parallel_config import resolve_active_session_key
    from miniagent.types.confirmation import ConfirmationResult, ConfirmationStage

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
    await runtime["inbound_turns"].submit(message, runtime["process_input"])
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

        from miniagent.engine.cli_completion import create_cli_completer  # noqa: F401
    except ImportError:
        await run_cli_loop_fallback(ctx, state, skill_toolboxes, skill_prompts)
        return
    if get_config("cli.force_fallback", False) or not sys.stdin.isatty() or not sys.stdout.isatty():
        await run_cli_loop_fallback(ctx, state, skill_toolboxes, skill_prompts)
        return
    from miniagent.engine.cli_tui_app import run_fullscreen_cli

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
