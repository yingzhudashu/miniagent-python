"""Engine — 主启动入口

拆分自 unified.py。

职责：
- 信号处理注册
- 子系统初始化
- CLI 主循环；可选同进程内启动飞书长轮询（无独立「纯飞书」入口）
- 优雅关闭（含子进程清理）
- 子进程清理（``cleanup_all_processes``）

依赖注入：``unified_main`` / ``run_cli_loop`` / 飞书 handler 工厂通过
:class:`miniagent.runtime.context.RuntimeContext` 获取 registry、monitor、engine 等，
勿再依赖 ``unified`` 模块级全局。

异步时序（队列 → Agent → 回复）见 ``docs/ARCHITECTURE.md``；点命令见 ``docs/CLI.md``。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # prompt_toolkit≥3.0.50 仅在类型检查块中定义该别名，运行时 key_bindings 无此名（勿在运行中 from … import）。
    from prompt_toolkit.key_binding.key_bindings import NotImplementedOrNone

from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.infrastructure.instance import (
    heartbeat,
    register_instance,
    unregister_instance,
)
from miniagent.infrastructure.logger import set_console_log_threshold
from miniagent.runtime.context import RuntimeContext

_logger = logging.getLogger(__name__)


def _copy_text_to_system_clipboard(text: str) -> bool:
    """将纯文本写入系统剪贴板（全屏 CLI 无法用鼠标框选 transcript 时可用）。"""
    if not text:
        return False
    te = text.replace("\r\n", "\n")
    try:
        if sys.platform == "win32":
            try:
                r = subprocess.run(
                    ["clip"],
                    input=te.encode("utf-16le"),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if r.returncode == 0:
                    return True
            except Exception:
                pass
            import ctypes

            GMEM_MOVEABLE = 0x0002
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return False
            try:
                if not user32.EmptyClipboard():
                    return False
                raw = te.encode("utf-16le") + b"\x00\x00"
                n = len(raw)
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n)
                if not h:
                    return False
                p = kernel32.GlobalLock(h)
                if not p:
                    kernel32.GlobalFree(h)
                    return False
                try:
                    ctypes.memmove(p, raw, n)
                finally:
                    kernel32.GlobalUnlock(h)
                if not user32.SetClipboardData(CF_UNICODETEXT, h):
                    kernel32.GlobalFree(h)
                    return False
                return True
            finally:
                user32.CloseClipboard()
        if sys.platform == "darwin":
            r = subprocess.run(
                ["pbcopy"],
                input=te.encode("utf-8"),
                capture_output=True,
                timeout=10,
                check=False,
            )
            return r.returncode == 0
        for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
            try:
                r = subprocess.run(
                    argv,
                    input=te.encode("utf-8"),
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                if r.returncode == 0:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def _feishu_user_status_fn(ctx: RuntimeContext) -> Callable[[str], None]:
    """飞书状态行：全屏已注册 ``cli_transcript_append`` 时写入 transcript，否则 print。"""

    def _emit(msg: str) -> None:
        """将飞书状态行写入全屏 transcript（样式 ``cli-muted``）或退回 ``print``。"""
        fn = ctx.cli_transcript_append
        line = msg if msg.endswith("\n") else msg + "\n"
        if fn is not None:
            try:
                fn("class:cli-muted", line)
            except Exception:
                print(msg, flush=True)
        else:
            print(msg, flush=True)

    return _emit


# ─── unified_main：RuntimeContext 注入后的进程主流程（init → 信号/实例 → CLI 循环 / 飞书任务）──


async def unified_main(ctx: RuntimeContext) -> None:
    """主启动流程。

    不再检查全局单实例 — 支持多实例并行。
    每个实例通过会话级 .lock 文件隔离。

    Args:
        ctx: 运行时组合根（registry / monitor / skill_registry / clawhub / engine）
    """
    # MINIAGENT_CONFIG 已在启动早期加载；勿在此处重复调用以免噪音。
    # 若测试或嵌入场景仅调用 unified_main，需自行先执行 load_external_config_from_env()。

    registry = ctx.registry
    skill_registry = ctx.skill_registry
    engine = ctx.engine
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        # 尝试启用 Windows VT 模式（某些终端可能不支持）
        try:
            import ctypes
            _h = ctypes.windll.kernel32.GetStdHandle(-11)
            if _h and _h != -1:
                _mode = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetConsoleMode(_h, ctypes.byref(_mode)):
                    _new_mode = _mode.value | 0x0004
                    ctypes.windll.kernel32.SetConsoleMode(_h, _new_mode)
        except Exception:
            pass  # VT 模式不可用，降级到 prompt_toolkit 颜色

    from miniagent.core.executor import MODEL
    from miniagent.engine.init import init_subsystems
    from miniagent.engine.welcome import print_welcome

    # 磁盘注册：分配 instance_id 前会清扫 PID 已失效的目录（不 kill 其它进程）
    feishu_mode = "--feishu" in sys.argv
    reg_result = register_instance(
        mode="both" if feishu_mode else "cli",
        active_sessions=[],
    )
    instance_id = reg_result.get("instance_id", 0)

    # 全局状态（通过闭包传递）
    state: CliLoopState = {
        "active_session_id": "",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": feishu_mode,
        "session_manager": None,
        "instance_id": instance_id,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }
    ctx.create_feishu_handler_factory = (
        lambda tb, tp, st: _create_feishu_handler(tb, tp, st, ctx)
    )

    # 信号：在事件循环线程内 await 统一关停（飞书 WS reset、子进程、实例注销）
    main_loop = asyncio.get_running_loop()
    _sig_lock = threading.Lock()
    _sig_armed: dict[str, bool] = {"v": False}

    async def _shutdown_after_signal(signum: int) -> None:
        """信号触发后在事件循环内执行 ``shutdown_runtime`` 并退出进程。"""
        await shutdown_runtime(
            ctx,
            state,
            reason=f"signal:{signum}",
            call_unregister=True,
            # 信号路径上 prompt_toolkit 可能仍占用 stdin/线程池；跳过默认线程池关闭以缩短竞态窗口
            shutdown_default_executor=False,
        )
        sys.exit(0)

    def _on_exit(signum: int, *_: Any) -> None:
        """信号处理器：防重入后把关停协程投递回主循环线程。"""
        with _sig_lock:
            if _sig_armed["v"]:
                os._exit(128)
            _sig_armed["v"] = True

        def _kick() -> None:
            """在主循环线程上调度 ``_shutdown_after_signal``。"""
            asyncio.create_task(_shutdown_after_signal(signum))

        main_loop.call_soon_threadsafe(_kick)

    signal.signal(signal.SIGINT, _on_exit)
    signal.signal(signal.SIGTERM, _on_exit)

    # 初始化子系统
    from miniagent.session.manager import DefaultSessionManager as SessionManager

    loaded_skills, skill_toolboxes, skill_prompts, active_session_id, session_manager = (
        await init_subsystems(
            registry,
            skill_registry,
            engine,
            SessionManager,
            ctx.channel_router,
            clawhub=ctx.clawhub,
            keyword_index=ctx.keyword_index,
        )
    )
    state["active_session_id"] = active_session_id
    state["skill_toolboxes"] = skill_toolboxes
    state["skill_prompts"] = skill_prompts
    state["session_manager"] = session_manager

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # 飞书与 CLI 共进程：先起 WS 长轮询任务，再进入同一 stdin 主循环（无单独纯飞书入口）
    if state["feishu_enabled"]:
        ctx.feishu.start(
            skill_toolboxes,
            skill_prompts,
            ctx.create_feishu_handler_factory,
            state,
            user_status=_feishu_user_status_fn(ctx),
        )

    from miniagent.scheduled_tasks.ticker import start_scheduled_tasks_ticker

    start_scheduled_tasks_ticker(ctx, state, skill_toolboxes, skill_prompts)

    # 显示欢迎信息
    print_welcome(
        registry,
        skill_registry,
        MODEL,
        os.environ.get("MODEL_PROFILE", "balanced"),
        state.get("session_manager"),
        active_session_id,
        state["feishu_enabled"],
    )

    # 运行 CLI 循环
    await run_cli_loop(
        ctx,
        state,
        skill_toolboxes,
        skill_prompts,
    )

    # run_cli_loop 正常返回后的收尾（异常路径依赖信号与 finally）
    await shutdown_runtime(
        ctx,
        state,
        reason="run_cli_loop_returned",
        abort_message_queues=True,
        release_cli_session_lock=False,
        call_unregister=False,
        shutdown_default_executor=True,
    )


# ─── run_cli_loop：prompt_toolkit 全屏/简化终端上的 stdin 主循环（点命令 → 队列 → Agent）──


async def run_cli_loop(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list,
    skill_prompts: list,
) -> None:
    """CLI 交互循环（使用 prompt_toolkit 实现固定输入区）。

    界面布局：
    ─────────── 分隔线 ───────────
    [Agent 输出区域]
    ─────────── 分隔线 ───────────
    ❯ [输入框，固定底部，支持历史]
    """
    engine = ctx.engine
    registry = ctx.registry
    monitor = ctx.monitor
    channel_router = ctx.channel_router
    message_queue = ctx.message_queue

    try:
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
    except ImportError:
        await _run_cli_loop_fallback(
            ctx, state, skill_toolboxes, skill_prompts,
        )
        return

    # 无 TTY（如 pytest 子进程重定向 stdin/stdout）时全屏 Application 无法初始化，回退到 input() 循环
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        await _run_cli_loop_fallback(
            ctx, state, skill_toolboxes, skill_prompts,
        )
        return

    # 历史记录文件
    history_dir = os.path.join(
        os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "workspaces")),
        "cli",
    )
    os.makedirs(history_dir, exist_ok=True)
    history_file = os.path.join(history_dir, "history.txt")

    # ── CLI 界面：底部固定输入框（类似 Claude Code） ──
    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import has_focus
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import (
        BufferControl,
        FormattedTextControl,
        UIContent,
        UIControl,
    )
    from prompt_toolkit.layout.dimension import LayoutDimension as D
    from prompt_toolkit.layout.scrollable_pane import ScrollablePane
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

    # #region agent log
    try:
        import json as _json
        import time as _time

        import prompt_toolkit as _pt

        _kb = __import__(
            "prompt_toolkit.key_binding.key_bindings", fromlist=["_probe"]
        )
        _repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        _log_path = os.path.join(_repo_root, "debug-f5825e.log")
        with open(_log_path, "a", encoding="utf-8") as _lf:
            _lf.write(
                _json.dumps(
                    {
                        "sessionId": "f5825e",
                        "runId": "post-fix",
                        "hypothesisId": "H1",
                        "location": "main.py:run_cli_loop:after_pt_imports",
                        "message": "prompt_toolkit CLI imports succeeded",
                        "data": {
                            "pt_version": getattr(_pt, "__version__", None),
                            "key_bindings_has_NotImplementedOrNone_runtime": hasattr(
                                _kb, "NotImplementedOrNone"
                            ),
                        },
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion

    from miniagent.engine.session_lock import release_session_lock

    _dbg_path = os.path.normpath(
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "debug-b7d9b9.log")
        )
    )

    input_buffer = Buffer(history=FileHistory(history_file))

    _MAX_TRANSCRIPT_CHARS = 400_000
    _transcript: list[Any] = []
    _stick_bottom: list[bool] = [True]

    # #region agent log
    _DBG_LOG_02 = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "debug-02b0ad.log",
    )
    _dbg_ph_remain: list[int] = [150]
    _dbg_pt_ver_done: list[bool] = [False]

    def _dbg_ndjson_02(
        hypothesis_id: str,
        location: str,
        message: str,
        data: dict,
        *,
        run_id: str = "post-fix",
    ) -> None:
        """调试 NDJSON 追加写入（存在则记录 hypothesis/布局信息；失败静默）。"""
        try:
            import json as _json
            import time as _time

            extra: dict = {}
            if not _dbg_pt_ver_done[0]:
                _dbg_pt_ver_done[0] = True
                try:
                    import prompt_toolkit as _pt

                    extra["prompt_toolkit_version"] = getattr(_pt, "__version__", None)
                except Exception:
                    extra["prompt_toolkit_version"] = None
            payload = {
                "sessionId": "02b0ad",
                "runId": run_id,
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": {**data, **extra},
                "timestamp": int(_time.time() * 1000),
            }
            with open(_DBG_LOG_02, "a", encoding="utf-8") as _df:
                _df.write(_json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _dbg_transcript_summary() -> dict:
        """返回 transcript 条数、类型分布与尾部类型名（供调试日志使用）。"""
        types: dict[str, int] = {}
        for f in _transcript:
            k = type(f).__name__
            types[k] = types.get(k, 0) + 1
        return {
            "len": len(_transcript),
            "types": types,
            "tail_type_names": [type(f).__name__ for f in _transcript[-8:]],
        }

    # #endregion

    def _transcript_fragment_len(frag: Any) -> int:
        """估算单条 transcript 片段的字符长度（tuple 文本或 ``ANSI`` 包裹串）。"""
        if isinstance(frag, tuple) and len(frag) >= 2:
            return len(frag[1])
        try:
            from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI

            if isinstance(frag, PTANSI):
                return len(frag.value)
        except Exception:
            pass
        return 0

    def _trim_transcript() -> None:
        """当总字符数超过上限时从头部丢弃片段，保留最近内容。"""
        total = sum(_transcript_fragment_len(f) for f in _transcript)
        while total > _MAX_TRANSCRIPT_CHARS and len(_transcript) > 16:
            old = _transcript.pop(0)
            total -= _transcript_fragment_len(old)

    def _flatten_transcript_for_pt() -> list[Any]:
        """Expand stored ``ANSI(...)`` rows to plain (style, text) fragments.

        ``to_formatted_text`` treats top-level lists as already normalized and does
        not recurse into items, so a mix of tuples and ``ANSI`` breaks ``split_lines``.
        """
        from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI
        from prompt_toolkit.formatted_text.base import to_formatted_text

        out: list[Any] = []
        for frag in _transcript:
            if isinstance(frag, tuple) and len(frag) >= 2:
                out.append(frag)
            elif isinstance(frag, PTANSI):
                out.extend(to_formatted_text(frag))
            else:
                out.extend(to_formatted_text(frag))
        return out

    _output_scroll_ref: list[Any] = [None]

    def _sp() -> Any:
        """当前 ``ScrollablePane`` 引用（输出区滚动容器）。"""
        return _output_scroll_ref[0]

    def _viewport_rows() -> int:
        """可用于输出区的近似行数（终端高度减去 chrome）。"""
        try:
            app = get_app()
            return max(6, (app.output.get_size().rows or 24) - 4)
        except Exception:
            return 20

    def _viewport_cols() -> int:
        """输出区可用列宽（扣除滚动条占位）。"""
        try:
            sp = _sp()
            if sp is None:
                return 79
            app = get_app()
            cols = max(40, app.output.get_size().columns or 80)
            sb = 1 if sp.show_scrollbar() else 0
            return max(1, cols - sb)
        except Exception:
            return 79

    def _content_preferred_height() -> int:
        """transcript 内容理想高度（用于计算最大滚动偏移）。"""
        try:
            sp = _sp()
            if sp is None:
                return 0
            ph = sp.content.preferred_height(
                _viewport_cols(), sp.max_available_height
            )
            return int(getattr(ph, "preferred", ph) or 0)
        except Exception:
            return 0

    def _max_output_scroll() -> int:
        """``vertical_scroll`` 合法上限：内容高度与视口行数之差。"""
        vh = _content_preferred_height()
        rows = _viewport_rows()
        return max(0, vh - rows)

    def _output_at_bottom() -> bool:
        """用户是否已滚动到输出区底部（决定是否自动粘底）。"""
        sp = _sp()
        if sp is None:
            return True
        return sp.vertical_scroll >= _max_output_scroll() - 1

    def _snap_output_bottom() -> None:
        """将输出区滚动条置底。"""
        sp = _sp()
        if sp is not None:
            sp.vertical_scroll = _max_output_scroll()

    def _wheel_line_step() -> int:
        """滚轮一次滚动的近似行数。"""
        return max(1, _viewport_rows() // 6)

    def _apply_transcript_scroll(signed_step: int, src: str) -> None:
        """signed_step<0: older; >0: newer. Drives ScrollablePane.vertical_scroll."""
        sp = _sp()
        if sp is None:
            return
        _stick_bottom[0] = False
        step = max(1, abs(signed_step))
        before = sp.vertical_scroll
        mx = _max_output_scroll()
        if signed_step < 0:
            sp.vertical_scroll = max(0, before - step)
        else:
            sp.vertical_scroll = min(mx, before + step)
        after = sp.vertical_scroll
        try:
            import json as _json
            import time as _time

            with open(_dbg_path, "a", encoding="utf-8") as _df:
                _df.write(
                    _json.dumps(
                        {
                            "sessionId": "b7d9b9",
                            "hypothesisId": "H-scroll",
                            "location": "main.py:_apply_transcript_scroll",
                            "message": "scroll",
                            "data": {
                                "src": src,
                                "signed_step": signed_step,
                                "step": step,
                                "before": before,
                                "after": after,
                                "max_scroll": mx,
                            },
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass

    class _TranscriptPaneControl(UIControl):
        """将滚轮从「内层 Window 自滚」转为 ScrollablePane.vertical_scroll。"""

        __slots__ = ("_inner",)

        def __init__(self, inner: FormattedTextControl) -> None:
            """包装内层 ``FormattedTextControl`` 以拦截鼠标滚轮事件。"""
            self._inner = inner

        def preferred_width(self, max_available_width: int) -> int | None:
            """委托内层宽度计算。"""
            return self._inner.preferred_width(max_available_width)

        def preferred_height(
            self,
            width: int,
            max_available_height: int,
            wrap_lines: bool,
            get_line_prefix,
        ) -> int | None:
            """委托内层高度计算（可选附带调试 NDJSON 采样）。"""
            # #region agent log
            if _dbg_ph_remain[0] > 0:
                _dbg_ph_remain[0] -= 1
                _dbg_ndjson_02(
                    "H1-H5",
                    "main.py:_TranscriptPaneControl.preferred_height",
                    "before inner.preferred_height",
                    {
                        "width": width,
                        "max_available_height": max_available_height,
                        "wrap_lines": wrap_lines,
                        "summary": _dbg_transcript_summary(),
                        "ph_logs_left_after": _dbg_ph_remain[0],
                    },
                )
            # #endregion
            return self._inner.preferred_height(
                width, max_available_height, wrap_lines, get_line_prefix
            )

        def create_content(self, width: int, height: int) -> UIContent:
            """委托内层生成 ``UIContent``。"""
            return self._inner.create_content(width, height)

        def mouse_handler(self, mouse_event: MouseEvent) -> NotImplementedOrNone:
            """滚轮事件改为驱动 ``ScrollablePane`` 纵向滚动，其余交给内层。"""
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                _apply_transcript_scroll(-_wheel_line_step(), "mouse.SCROLL_UP")
                get_app().invalidate()
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                _apply_transcript_scroll(_wheel_line_step(), "mouse.SCROLL_DOWN")
                get_app().invalidate()
                return None
            return self._inner.mouse_handler(mouse_event)

    transcript_inner = FormattedTextControl(
        text=_flatten_transcript_for_pt,
        focusable=False,
    )
    transcript_window = Window(
        _TranscriptPaneControl(transcript_inner),
        wrap_lines=True,
    )
    output_scroll = ScrollablePane(
        transcript_window,
        height=D(weight=1),
        keep_cursor_visible=False,
        keep_focused_window_visible=False,
        show_scrollbar=True,
    )
    _output_scroll_ref[0] = output_scroll

    def _append_transcript(style_cls: str, text: str) -> None:
        """向 transcript 追加样式化文本；同样式尾部合并；维护粘底与长度裁剪。"""
        if not text:
            return
        at_bottom = _output_at_bottom()
        if (
            _transcript
            and isinstance(_transcript[-1], tuple)
            and len(_transcript[-1]) >= 2
            and _transcript[-1][0] == style_cls
        ):
            st, prev = _transcript[-1]
            _transcript[-1] = (st, prev + text)
        else:
            _transcript.append((style_cls, text))
        _trim_transcript()
        try:
            get_app().invalidate()
        except Exception:
            pass
        if at_bottom or _stick_bottom[0]:
            _snap_output_bottom()
            if at_bottom:
                _stick_bottom[0] = True
        else:
            _stick_bottom[0] = False

    def _transcript_plain() -> str:
        """将当前 transcript 转为纯文本（剥离 ANSI，用于复制等）。"""
        from miniagent.engine.markdown_cli import strip_ansi

        parts: list[str] = []
        for frag in _transcript:
            if isinstance(frag, tuple) and len(frag) >= 2:
                parts.append(frag[1])
            else:
                try:
                    from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI

                    if isinstance(frag, PTANSI):
                        parts.append(strip_ansi(frag.value))
                except Exception:
                    pass
        return "".join(parts)

    kb = KeyBindings()

    @kb.add("enter", filter=has_focus(input_buffer))
    def _on_enter(event):
        """回车提交输入"""
        text = input_buffer.text.strip()
        if text:
            input_buffer.reset(append_to_history=True)
            event.app.exit(result=text)

    @kb.add("c-c", filter=has_focus(input_buffer))
    def _on_ctrl_c(event):
        """Ctrl+C 退出"""
        event.app.exit(result="__exit__")

    def _scroll_step() -> int:
        """PageUp/PageDown 一次滚动的行数（约为半屏）。"""
        return max(1, _viewport_rows() // 2)

    @kb.add("pageup", filter=has_focus(input_buffer))
    def _on_pageup(event):
        """上翻输出区约半屏。"""
        _stick_bottom[0] = False
        output_scroll.vertical_scroll = max(0, output_scroll.vertical_scroll - _scroll_step())
        event.app.invalidate()

    @kb.add("pagedown", filter=has_focus(input_buffer))
    def _on_pagedown(event):
        """下翻输出区约半屏。"""
        _stick_bottom[0] = False
        output_scroll.vertical_scroll = min(
            _max_output_scroll(), output_scroll.vertical_scroll + _scroll_step()
        )
        event.app.invalidate()

    @kb.add("c-home", filter=has_focus(input_buffer))
    def _on_ctrl_home(event):
        """输出区滚到顶部。"""
        _stick_bottom[0] = False
        output_scroll.vertical_scroll = 0
        event.app.invalidate()

    @kb.add("c-end", filter=has_focus(input_buffer))
    def _on_ctrl_end(event):
        """输出区滚到底并恢复粘底。"""
        _stick_bottom[0] = True
        _snap_output_bottom()
        event.app.invalidate()

    # 无坐标的滚轮（Windows 控制台等）默认会变成 Up/Down 只作用于输入框；eager 优先改为滚动 transcript。
    @kb.add(Keys.ScrollUp, eager=True, filter=has_focus(input_buffer))
    def _on_scroll_up_key(event):
        """无坐标滚轮映射为 Up：向上滚动 transcript。"""
        _apply_transcript_scroll(-_wheel_line_step(), "keys.ScrollUp")
        event.app.invalidate()

    @kb.add(Keys.ScrollDown, eager=True, filter=has_focus(input_buffer))
    def _on_scroll_down_key(event):
        """无坐标滚轮映射为 Down：向下滚动 transcript。"""
        _apply_transcript_scroll(_wheel_line_step(), "keys.ScrollDown")
        event.app.invalidate()

    # PT 的 _parse_style_str 只认属性词 "dim"，不认 "ansidim"（后者会走 parse_color → ValueError）。
    _cli_style_dict = {
        "prompt-prefix": "bold ansigreen",
        "cli-border-strong": "ansibrightblue bold",
        "cli-border": "ansiblue dim",
        "cli-user-title": "bold ansicyan",
        "cli-user-body": "ansicyan",
        "cli-think-head": "bold ansibrightblack",
        "cli-think-body": "ansibrightblack dim",
        "cli-assistant-title": "bold ansigreen",
        "cli-assistant-body": "ansigreen",
        "cli-default": "",
        "cli-muted": "ansibrightblack dim",
        "cli-ok": "ansigreen",
        "cli-err": "ansired bold",
        "cli-warn": "ansiyellow",
        "cli-hint": "ansibrightblack dim",
        "cli-spacer": "",
    }
    # #region agent log
    import json
    import time as _time

    try:
        with open(_dbg_path, "a", encoding="utf-8") as _df:
            _df.write(
                json.dumps(
                    {
                        "sessionId": "b7d9b9",
                        "hypothesisId": "H1",
                        "location": "miniagent/engine/main.py:cli_style",
                        "message": "before_from_dict",
                        "data": {
                            "has_ansidim": any(
                                "ansidim" in str(v) for v in _cli_style_dict.values()
                            ),
                            "sample_vals": list(_cli_style_dict.values())[:4],
                        },
                        "timestamp": int(_time.time() * 1000),
                        "runId": "post-fix",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    cli_style = Style.from_dict(_cli_style_dict)
    # #region agent log
    try:
        with open(_dbg_path, "a", encoding="utf-8") as _df:
            _df.write(
                json.dumps(
                    {
                        "sessionId": "b7d9b9",
                        "hypothesisId": "H1-verify",
                        "location": "miniagent/engine/main.py:cli_style",
                        "message": "from_dict_ok",
                        "data": {"style_type": type(cli_style).__name__},
                        "timestamp": int(_time.time() * 1000),
                        "runId": "post-fix",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion

    body = HSplit(
        [
            output_scroll,
            Window(
                FormattedTextControl(
                    HTML(
                        "<cli-hint>PgUp/PgDn · \u6eda\u8f6e · "
                        "Ctrl+Home/End · .copy \u590d\u5236\u5168\u90e8\u5bf9\u8bdd · "
                        "\u65b0\u6d88\u606f\u65f6\u81ea\u52a8\u8ddf\u968f\u8f93\u51fa</cli-hint>"
                    )
                ),
                height=D.exact(1),
            ),
            Window(height=1, char="\u2500", style="class:cli-border"),
            VSplit(
                [
                    Window(
                        FormattedTextControl(
                            HTML("<prompt-prefix>\u276f </prompt-prefix>")
                        ),
                        width=D.exact(4),
                        height=D.exact(1),
                    ),
                    Window(
                        BufferControl(buffer=input_buffer),
                        height=D.exact(1),
                        wrap_lines=False,
                    ),
                ],
                height=D.exact(1),
            ),
        ],
    )

    layout = Layout(body, focused_element=input_buffer)

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=True,
        style=cli_style,
    )
    ctx.cli_transcript_append = _append_transcript
    # stderr 日志仍会打乱 VS Code 等与 PT 共用的终端画布；TUI 期间默认只打 WARNING+
    if not os.environ.get("MINI_AGENT_TUI_VERBOSE_LOG"):
        set_console_log_threshold(logging.WARNING)

    _LEGACY_COLOR_CLASS: dict[str, str] = {
        "ansiblue": "class:cli-border",
        "ansigreen": "class:cli-ok",
        "ansired": "class:cli-err",
        "ansiyellow": "class:cli-warn",
    }

    def term_write(text: str = "", color: str = "") -> None:
        """写入上方 transcript（样式类，非裸 ANSI）。"""
        if text == "":
            return
        style = _LEGACY_COLOR_CLASS.get(color, "class:cli-default")
        if not text.endswith("\n"):
            text = text + "\n"
        _append_transcript(style, text)

    def _thinking_sink(
        fragment: str,
        kind: str = "chunk",
        *,
        ansi_markdown: str | None = None,
    ) -> None:
        """``ThinkingDisplay`` 输出槽：写入思考标签/正文或整段 Rich 渲染后的 ANSI。"""
        if ansi_markdown is not None:
            from prompt_toolkit.formatted_text import ANSI

            body_lines = ansi_markdown.rstrip("\n").split("\n")
            transcript_body = "\n".join(ln if ln else "" for ln in body_lines) + "\n"
            at_bottom = _output_at_bottom()
            _transcript.append(ANSI(transcript_body))
            _trim_transcript()
            try:
                get_app().invalidate()
            except Exception:
                pass
            if at_bottom or _stick_bottom[0]:
                _snap_output_bottom()
                if at_bottom:
                    _stick_bottom[0] = True
            else:
                _stick_bottom[0] = False
            return
        style = "class:cli-think-head" if kind == "label" else "class:cli-think-body"
        _append_transcript(style, fragment)

    engine.thinking.set_output_sink(_thinking_sink)
    engine.thinking.set_cli_markdown_width(lambda: max(40, _viewport_cols() - 2))

    def _rule_line_width() -> int:
        """与 Markdown 渲染宽度同源，避免分隔线与正文视觉错位。"""
        return max(40, _viewport_cols())

    def _cli_rule_heavy() -> None:
        """在 transcript 中画粗分隔线（双线条字符）。"""
        w = _rule_line_width()
        _append_transcript("class:cli-border-strong", "\u2550" * w + "\n")

    def _cli_rule_light() -> None:
        """在 transcript 中画细分隔线。"""
        w = _rule_line_width()
        _append_transcript("class:cli-border", "\u2500" * w + "\n")

    def _cli_block_user(prompt: str) -> None:
        """本轮提问区块。"""
        _stick_bottom[0] = True
        _append_transcript("class:cli-spacer", "\n")
        _cli_rule_heavy()
        _append_transcript("class:cli-user-title", "You\n")
        _cli_rule_light()
        for line in (prompt or "").splitlines() or [""]:
            _append_transcript("class:cli-user-body", line + "\n")
        _append_transcript("class:cli-spacer", "\n")

    def _cli_block_reply(text: str) -> None:
        """最终回复区块（可选 Rich → ANSI，经 prompt_toolkit 解析）。"""
        from prompt_toolkit.formatted_text import ANSI

        from miniagent.engine.markdown_cli import render_markdown_to_ansi

        _append_transcript("class:cli-spacer", "\n")
        _cli_rule_light()
        _append_transcript("class:cli-assistant-title", "Assistant\n")
        _cli_rule_light()
        md_w = max(40, _viewport_cols() - 2)
        ansi_body = render_markdown_to_ansi(text or "", width=md_w)
        if ansi_body and ansi_body.strip():
            at_bottom = _output_at_bottom()
            body_lines = ansi_body.rstrip("\n").split("\n")
            transcript_body = "\n".join(ln if ln else "" for ln in body_lines) + "\n"
            _transcript.append(ANSI(transcript_body))
            # #region agent log
            _dbg_ndjson_02(
                "H1-H2",
                "main.py:_cli_block_reply",
                "appended ANSI fragment to transcript",
                {
                    "ansi_chars": len(transcript_body),
                    "summary_after": _dbg_transcript_summary(),
                    "flatten_out_len": len(_flatten_transcript_for_pt()),
                },
            )
            # #endregion
            _trim_transcript()
            try:
                get_app().invalidate()
            except Exception:
                pass
            if at_bottom or _stick_bottom[0]:
                _snap_output_bottom()
                if at_bottom:
                    _stick_bottom[0] = True
            else:
                _stick_bottom[0] = False
        else:
            for line in (text or "").splitlines() or [""]:
                _append_transcript("class:cli-assistant-body", line + "\n")
        _append_transcript("class:cli-spacer", "\n")
        _cli_rule_heavy()

    async def _process_input(user_input: str) -> None:
        """处理用户输入并打印回复。"""
        try:
            session_key = channel_router.resolve("__cli__")
            # 新输入开始：先贴上一轮底部，再画本轮 You 块，避免仍停在上次上滚位置。
            _stick_bottom[0] = True
            try:
                _snap_output_bottom()
                get_app().invalidate()
            except Exception:
                pass
            _cli_block_user(user_input)
            try:
                await asyncio.sleep(0)
                _stick_bottom[0] = True
                _snap_output_bottom()
                get_app().invalidate()
            except Exception:
                pass
            reply = await engine.run_agent_with_thinking(
                user_input,
                session_key,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                registry=registry,
                monitor=monitor,
                session_manager=state.get("session_manager"),
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                cli_loop_state=state,
            )
            _cli_block_reply(reply)
        except Exception as e:
            _append_transcript("class:cli-err", f"\u274c \u9519\u8bef: {e}\n")

    while True:
        try:
            user_input = await app.run_async()
        except EOFError:
            break
        except Exception as exc:
            _logger.warning(
                "全屏 CLI (prompt_toolkit) 异常，改用常规 input 模式: %s",
                exc,
                exc_info=True,
            )
            set_console_log_threshold(logging.INFO)
            ctx.cli_transcript_append = None
            await _run_cli_loop_fallback(
                ctx, state, skill_toolboxes, skill_prompts,
            )
            return
        if user_input == "__exit__":
            break
        if user_input is None:
            continue

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        # ── .copy（全屏区为 FormattedText，终端一般无法框选复制）──
        if user_input == ".copy":
            plain = _transcript_plain()
            if _copy_text_to_system_clipboard(plain):
                term_write(
                    f"\u2705 \u5df2\u590d\u5236 {len(plain)} \u5b57\u7b26\u5230\u526a\u8d34\u677f\n",
                    "ansigreen",
                )
            else:
                term_write(
                    "\u274c \u590d\u5236\u5931\u8d25\uff08\u65e0\u526a\u8d34\u677f\u6216\u7f3a\u5c11 "
                    "wl-copy / xclip / pbcopy / clip\uff09\n",
                    "ansired",
                )
            continue

        # ── .stop ──
        if user_input == ".stop":
            await shutdown_runtime(
                ctx,
                state,
                reason="dot_stop_ptk",
                release_cli_session_lock=True,
                call_unregister=True,
            )
            term_write("\u2705 \u5f53\u524d\u5b9e\u4f8b\u5df2\u505c\u6b62", "ansigreen")
            sys.exit(0)

        # ── 其余点命令：统一走 dispatch（capture → transcript，避免 print 破坏全屏）──
        if user_input.startswith("."):
            from miniagent.engine.command_dispatch import dispatch_command

            reply = await dispatch_command(
                user_input,
                state=state,
                engine=engine,
                registry=registry,
                monitor=monitor,
                skill_toolboxes=skill_toolboxes,
                skill_prompts=skill_prompts,
                capture=True,
                allow_session_mutations_when_capture=True,
                feishu_user_status=_feishu_user_status_fn(ctx),
            )
            if reply is not None:
                term_write(reply + "\n")
                continue

        # ── Agent 执行 ──
        await message_queue.dispatch_cli(_process_input(user_input))

        try:
            heartbeat()
        except Exception:
            pass

    # 清理
    set_console_log_threshold(logging.INFO)
    ctx.cli_transcript_append = None
    release_session_lock(state["active_session_id"])
    try:
        unregister_instance()
    except Exception:
        pass
    # 全屏 Application 已结束；直接打印告别
    print("\n\U0001f44b bye\n", file=sys.stdout, flush=True)


async def _run_cli_loop_fallback(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list,
    skill_prompts: list,
) -> None:
    """简易 CLI 循环（prompt_toolkit 不可用时回退）。"""
    engine = ctx.engine
    registry = ctx.registry
    monitor = ctx.monitor
    channel_router = ctx.channel_router
    message_queue = ctx.message_queue
    from miniagent.core.config import MODEL_PROFILES
    from miniagent.engine.cli_commands import (
        cmd_help,
        cmd_instance_handler,
        cmd_queue_set,
        cmd_queue_status,
        cmd_session_create,
        cmd_session_list,
        cmd_session_rename,
        cmd_session_switch,
        format_queue_command_usage,
        format_session_command_usage,
    )
    from miniagent.engine.session_lock import (
        is_session_locked,
        release_session_lock,
        try_lock_session,
    )

    active_profile = os.environ.get("MODEL_PROFILE", "balanced")

    _fb_w = 60

    def _fb_rule_heavy() -> None:
        """非全屏 CLI 下的粗分隔线（stdout）。"""
        print("\u2550" * _fb_w)

    def _fb_rule_light() -> None:
        """非全屏 CLI 下的细分隔线（stdout）。"""
        print("\u2500" * _fb_w)

    async def _process_input(user_input: str) -> None:
        """备用终端：打印 You/Assistant 区块并调用 ``run_agent_with_thinking``。"""
        try:
            session_key = channel_router.resolve("__cli__")
            print()
            _fb_rule_heavy()
            print("You")
            _fb_rule_light()
            for line in (user_input or "").splitlines() or [""]:
                print(line)
            print()
            reply = await engine.run_agent_with_thinking(
                user_input,
                session_key,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                registry=registry,
                monitor=monitor,
                session_manager=state.get("session_manager"),
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                cli_loop_state=state,
            )
            print()
            _fb_rule_light()
            print("Assistant")
            _fb_rule_light()
            from miniagent.engine.markdown_cli import cli_raw_markdown_enabled

            fb_w = max(40, shutil.get_terminal_size(fallback=(80, 24)).columns - 4)
            if cli_raw_markdown_enabled():
                for line in (reply or "").splitlines() or [""]:
                    print(line)
            else:
                try:
                    from rich.console import Console
                    from rich.markdown import Markdown

                    Console(soft_wrap=True, width=fb_w).print(Markdown(reply or ""))
                except ImportError:
                    for line in (reply or "").splitlines() or [""]:
                        print(line)
            print()
            _fb_rule_heavy()
        except Exception as e:
            print(f"\n\u274c \u9519\u8bef: {e}")

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\n\u276f ")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        if user_input == ".copy":
            print(
                "\n\u63d0\u793a: \u7b80\u6613\u6a21\u5f0f\u4e0b\u8f93\u51fa\u5728\u7ec8\u7aef\u5377\u8f74"
                "\uff0c\u8bf7\u7528\u7ec8\u7aef\u81ea\u8eab\u9009\u62e9\u590d\u5236"
                "\uff1b\u5168\u5c4f CLI \u4e0b\u8f93\u5165 .copy \u53ef\u590d\u5236 transcript\u3002\n"
            )
            continue

        if user_input == ".stop":
            await shutdown_runtime(
                ctx,
                state,
                reason="dot_stop_fallback",
                release_cli_session_lock=True,
                call_unregister=True,
            )
            print("\u2705 \u5f53\u524d\u5b9e\u4f8b\u5df2\u505c\u6b62")
            sys.exit(0)

        if user_input.startswith(".instance"):
            parts = user_input.split()
            sub_cmd = parts[1] if len(parts) > 1 else ""
            cmd_instance_handler(parts, sub_cmd, state)
            continue

        if user_input.startswith(".session "):
            parts = user_input.split()
            sub_cmd = parts[1] if len(parts) > 1 else ""
            if sub_cmd == "list":
                cmd_session_list(state.get("session_manager"), state["active_session_id"])
            elif sub_cmd == "switch" and len(parts) >= 3:
                state["active_session_id"] = await cmd_session_switch(
                    state.get("session_manager"),
                    state["active_session_id"],
                    parts[2],
                    try_lock_session,
                    release_session_lock,
                    is_session_locked,
                    channel_router,
                    state.get("feishu_p2p_synced_senders"),
                )
            elif sub_cmd == "create" and len(parts) >= 3:
                await cmd_session_create(
                    state.get("session_manager"),
                    parts[2],
                    parts[3] if len(parts) > 3 else None,
                    try_lock_session,
                )
            elif sub_cmd == "rename" and len(parts) >= 4:
                cmd_session_rename(state.get("session_manager"), parts[2], " ".join(parts[3:]))
            else:
                print(format_session_command_usage() + "\n")
            continue

        if user_input.startswith(".feishu"):
            if user_input == ".feishu start":
                ctx.feishu.start(
                    skill_toolboxes,
                    skill_prompts,
                    ctx.create_feishu_handler_factory,
                    state,
                    user_status=_feishu_user_status_fn(ctx),
                )
            elif user_input == ".feishu stop":
                await ctx.feishu.stop_async()
            else:
                ctx.feishu.status()
            continue

        if user_input.startswith(".queue"):
            parts = user_input.split()
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "status":
                cmd_queue_status(message_queue)
            elif sub == "set" and len(parts) >= 3:
                await cmd_queue_set(message_queue, parts[2])
            elif sub == "abort":
                from miniagent.engine.cli_commands import format_queue_abort_message

                res = message_queue.abort_chat(message_queue.CLI_CHAT_ID)
                print(format_queue_abort_message(res) + "\n")
            else:
                print(format_queue_command_usage(message_queue) + "\n")
            continue

        if user_input == ".abort":
            from miniagent.engine.cli_commands import format_queue_abort_message

            res = message_queue.abort_chat(message_queue.CLI_CHAT_ID)
            print(format_queue_abort_message(res) + "\n")
            continue

        if user_input == ".stats":
            print(f"\n{monitor.report()}")
            continue

        if user_input == ".status":
            from miniagent.engine.command_dispatch import _format_status as _fmt_status
            print(_fmt_status(state))
            continue

        if user_input.startswith(".profile"):
            parts = user_input.split()
            if len(parts) >= 2 and parts[1] in MODEL_PROFILES:
                active_profile = parts[1]
                os.environ["MODEL_PROFILE"] = active_profile
                print(f"\U0001f4e1 \u5df2\u5207\u6362\u5230\u9884\u8bbe: {parts[1]}")
            else:
                print(f"\u5f53\u524d\u9884\u8bbe: {active_profile}")
                print("\u53ef\u7528: " + ", ".join(MODEL_PROFILES.keys()))
            continue

        if user_input == ".help":
            cmd_help(MODEL_PROFILES, active_profile, message_queue, state.get("instance_id"))
            continue

        await message_queue.dispatch_cli(_process_input(user_input))

        try:
            heartbeat()
        except Exception:
            pass

    release_session_lock(state["active_session_id"])
    try:
        unregister_instance()
    except Exception:
        pass
    print("\n\U0001f44b bye")


def _create_feishu_handler(
    skill_toolboxes,
    skill_prompts,
    state: CliLoopState,
    ctx: RuntimeContext,
):
    """创建飞书消息处理器。

    飞书消息以 `.` 开头时，路由到统一命令调度器（与 CLI 共享）。
    通过 ChannelRouter 解析 session_key：
    - 群聊消息: 始终独立会话
    - 私聊消息: 检查是否绑定到 CLI 会话（支持干预）
    """
    engine = ctx.engine
    registry = ctx.registry
    monitor = ctx.monitor
    from miniagent.engine.cli_commands import feishu_dot_commands_full_enabled
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.feishu.types import FeishuInboundText

    channel_router = ctx.channel_router
    _emit_feishu_cli = _feishu_user_status_fn(ctx)

    async def handler(inbound: FeishuInboundText) -> str:
        """处理单条飞书消息（:class:`~miniagent.feishu.types.FeishuInboundText`）。

        以 `.` 开头的消息路由到统一命令调度器（与 CLI 共享）。
        普通消息通过 ChannelRouter 解析 session_key 后交给 Agent 处理。
        """
        content = inbound.text
        chat_id = inbound.chat_id
        sender_id = inbound.sender_id
        chat_type = inbound.chat_type or "group"

        if not engine:
            return "\u26a0\ufe0f \u5f15\u64ce\u672a\u521d\u59cb\u5316"

        # ── 命令拦截 ──
        if content.startswith("."):
            try:
                reply = await dispatch_command(
                    content.strip(),
                    state=state,
                    engine=engine,
                    registry=registry,
                    monitor=monitor,
                    skill_toolboxes=skill_toolboxes,
                    skill_prompts=skill_prompts,
                    capture=True,
                    allow_session_mutations_when_capture=feishu_dot_commands_full_enabled(),
                    message_queue_abort_chat_id=chat_id,
                )
                if reply is not None:
                    _emit_feishu_cli(
                        f"\n\U0001f4e8 [\u98de\u4e66\u547d\u4ee4 {chat_id[:8]}] {content}"
                    )
                    return reply
            except Exception as e:
                return f"\u274c \u547d\u4ee4\u6267\u884c\u5931\u8d25: {e}"

        # ── 飞书私聊：与 CLI 共用当前活跃会话（自动绑定；手动 .bind feishu 会从同步集合移除）
        if chat_type == "p2p":
            cid = f"{channel_router.FEISHU_P2P_PREFIX}{sender_id}"
            synced: set[str] = state.setdefault("feishu_p2p_synced_senders", set())  # type: ignore[assignment]
            if not isinstance(synced, set):
                synced = set()
                state["feishu_p2p_synced_senders"] = synced
            if not channel_router.is_bound(cid):
                act = (state.get("active_session_id") or "").strip()
                if act:
                    channel_router.bind(cid, act)
                    synced.add(sender_id)

        # ── 解析 session_key ──
        session_key = channel_router.resolve_feishu_message(
            chat_id, sender_id, chat_type
        )
        if (chat_id or "").strip():
            state["last_feishu_receive_chat_id"] = chat_id.strip()

        if chat_type == "p2p" and channel_router.is_bound(
            f"feishu_p2p:{sender_id}"
        ):
            primary = channel_router.primary
            _emit_feishu_cli(
                f"\n\U0001f4e8 [\u98de\u4e66\u79c1\u804a\u2192{primary[:12]}] {content}"
            )
        else:
            _emit_feishu_cli(f"\n\U0001f4e8 [\u98de\u4e66 {chat_id[:8]}] {content}")

        try:
            reply = await engine.run_agent_with_thinking(
                content,
                session_key,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                is_feishu=True,
                registry=registry,
                monitor=monitor,
                session_manager=state.get("session_manager"),
                feishu_config=ctx.feishu.get_config(),
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                feishu_receive_chat_id=chat_id,
                feishu_trigger_message_id=inbound.message_id or None,
                feishu_root_id=inbound.root_id,
                feishu_parent_id=inbound.parent_id,
                feishu_thread_id=inbound.thread_id,
                feishu_im_receive_id=(inbound.sender_id or "").strip() or None,
                cli_loop_state=state,
            )
            return reply
        except Exception as e:
            return f"\u26a0\ufe0f \u5904\u7406\u5931\u8d25: {e}"

    async def media_handler(
        cfg: Any,
        message_id: str,
        chat_id: str,
        sender_id: str,
        chat_type: str,
        msg_type: str,
        file_key: str,
        suggested_name: str,
        resource_type: str,
        thread_id: str | None = None,
    ) -> str | None:
        """下载飞书 file/image 到当前会话 workspace/files/feishu_incoming/。"""
        from miniagent.feishu.resource_io import download_message_resource, sanitize_filename
        from miniagent.types.memory import SessionOptions

        if not engine:
            return "\u26a0\ufe0f \u5f15\u64ce\u672a\u521d\u59cb\u5316"

        sm = state.get("session_manager")
        if sm is None:
            return "\u26a0\ufe0f \u4f1a\u8bdd\u7ba1\u7406\u5668\u672a\u521d\u59cb\u5316\uff0c\u65e0\u6cd5\u4fdd\u5b58\u6587\u4ef6"

        if chat_type == "p2p":
            cid = f"{channel_router.FEISHU_P2P_PREFIX}{sender_id}"
            synced: set[str] = state.setdefault("feishu_p2p_synced_senders", set())  # type: ignore[assignment]
            if not isinstance(synced, set):
                synced = set()
                state["feishu_p2p_synced_senders"] = synced
            if not channel_router.is_bound(cid):
                act = (state.get("active_session_id") or "").strip()
                if act:
                    channel_router.bind(cid, act)
                    synced.add(sender_id)

        session_key = channel_router.resolve_feishu_message(
            chat_id, sender_id, chat_type
        )
        sess = sm.get_or_create(
            session_key,
            SessionOptions(description="\u98de\u4e66\u5a92\u4f53\u5165\u7ad9"),
        )
        base = (sess.workspace_path or "").strip()
        if not base:
            return "\u26a0\ufe0f \u4f1a\u8bdd\u5de5\u4f5c\u533a\u672a\u914d\u7f6e\uff0c\u65e0\u6cd5\u5199\u5165\u6587\u4ef6"

        incoming = os.path.join(base, "feishu_incoming")
        os.makedirs(incoming, exist_ok=True)

        safe = sanitize_filename(suggested_name)
        root, ext = os.path.splitext(safe)
        tag = (message_id or "msg").replace("/", "_")[:16]
        dest_name = f"{root}_{tag}{ext}" if root else f"file_{tag}{ext or '.bin'}"
        dest_path = os.path.join(incoming, dest_name)

        if resource_type not in ("file", "image"):
            return "\u26a0\ufe0f \u4e0d\u652f\u6301\u7684\u8d44\u6e90\u7c7b\u578b"

        try:
            data, _header_name = await download_message_resource(
                cfg.app_id,
                cfg.app_secret,
                message_id=message_id,
                file_key=file_key,
                type_=resource_type,
            )
        except Exception as e:
            return f"\u26a0\ufe0f \u4e0b\u8f7d\u5931\u8d25: {e}"

        with open(dest_path, "wb") as f:
            f.write(data)

        try:
            rel = os.path.relpath(dest_path, base)
        except ValueError:
            rel = os.path.basename(dest_path)

        _emit_feishu_cli(
            f"\n\U0001f4ce [\u98de\u4e66\u5a92\u4f53 {chat_id[:8]}] \u5df2\u4fdd\u5b58: {rel}"
        )

        flag = (os.environ.get("MINIAGENT_FEISHU_MEDIA_RUN_AGENT") or "").strip().lower()
        run_agent_on_media = flag in ("1", "true", "yes", "on")
        if not run_agent_on_media:
            return f"\u2705 \u5df2\u4fdd\u5b58\u5230\u4f1a\u8bdd\u6587\u4ef6\u533a: {rel}"

        user_line = (
            f"[\u98de\u4e66\u5165\u7ad9] \u5df2\u4fdd\u5b58\u5a92\u4f53\u5230\u4f1a\u8bdd\u76ee\u5f55\uff08\u76f8\u5bf9 files \uff09: {rel}\n"
            f"\u8bf7\u67e5\u770b\u8be5\u6587\u4ef6\u5e76\u8bf4\u660e\u4f60\u53ef\u4ee5\u5982\u4f55\u534f\u52a9\u5904\u7406\u3002"
        )
        try:
            reply = await engine.run_agent_with_thinking(
                user_line,
                session_key,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                is_feishu=True,
                registry=registry,
                monitor=monitor,
                session_manager=sm,
                feishu_config=ctx.feishu.get_config(),
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                feishu_receive_chat_id=chat_id,
                feishu_trigger_message_id=message_id or None,
                feishu_thread_id=(thread_id or "").strip() or None,
                feishu_im_receive_id=(sender_id or "").strip() or None,
                cli_loop_state=state,
            )
            return reply
        except Exception as e:
            return f"\u2705 \u5df2\u4fdd\u5b58 {rel}\uff08Agent \u5904\u7406\u5931\u8d25: {e}\uff09"

    return handler, media_handler


__all__ = ["unified_main", "run_cli_loop"]
