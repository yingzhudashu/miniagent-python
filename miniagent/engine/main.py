"""Engine — 主启动入口

拆分自 unified.py。

职责：
- 信号处理注册
- 子系统初始化
- CLI 主循环；可选同进程内启动飞书长轮询（无独立「纯飞书」入口）
- 优雅关闭（含子进程清理）
- 集成 process_tracker 孤儿进程清理

依赖注入：``unified_main`` / ``run_cli_loop`` / 飞书 handler 工厂通过
:class:`miniagent.runtime.context.RuntimeContext` 获取 registry、monitor、engine 等，
勿再依赖 ``unified`` 模块级全局。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    # prompt_toolkit≥3.0.50 仅在类型检查块中定义该别名，运行时 key_bindings 无此名（勿在运行中 from … import）。
    from prompt_toolkit.key_binding.key_bindings import NotImplementedOrNone

from miniagent.infrastructure.logger import set_console_log_threshold
from miniagent.infrastructure.process import cleanup_all_processes
from miniagent.infrastructure.instance import (
    heartbeat,
    register_instance,
    unregister_instance,
)
from miniagent.engine.cli_state import CliLoopState
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


async def unified_main(ctx: RuntimeContext) -> None:
    """主启动流程。

    不再检查全局单实例 — 支持多实例并行。
    每个实例通过会话级 .lock 文件隔离。

    Args:
        ctx: 运行时组合根（registry / monitor / skill_registry / clawhub / engine）
    """
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

    # 注册多实例
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
    }
    ctx.create_feishu_handler_factory = (
        lambda tb, tp, st: _create_feishu_handler(tb, tp, st, ctx)
    )

    # 注册信号处理器
    def _on_exit(*_: Any) -> None:
        from miniagent.engine.session_lock import release_session_lock

        if state["active_session_id"]:
            release_session_lock(state["active_session_id"])
        task = ctx.feishu.get_task()
        if task:
            task.cancel()
            ctx.feishu.set_task(None)
        sys.exit(0)

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

    # 若带 --feishu：同进程内拉起飞书（随后仍进入 CLI 主循环）
    if state["feishu_enabled"]:
        ctx.feishu.start(
            skill_toolboxes,
            skill_prompts,
            ctx.create_feishu_handler_factory,
            state,
            user_status=_feishu_user_status_fn(ctx),
        )

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

    # 清理
    from miniagent.engine.session_lock import release_session_lock

    task = ctx.feishu.get_task()
    if task:
        task.cancel()
    release_session_lock(state["active_session_id"])

    # 清理子进程
    await cleanup_all_processes()


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
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
        from prompt_toolkit.formatted_text import HTML
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
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import (
        BufferControl,
        FormattedTextControl,
        UIControl,
        UIContent,
    )
    from prompt_toolkit.layout.dimension import LayoutDimension as D
    from prompt_toolkit.layout.scrollable_pane import ScrollablePane
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.filters import has_focus
    from prompt_toolkit.keys import Keys
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
    _transcript: list[tuple[str, str]] = []
    _stick_bottom: list[bool] = [True]

    def _trim_transcript() -> None:
        total = sum(len(t) for _, t in _transcript)
        while total > _MAX_TRANSCRIPT_CHARS and len(_transcript) > 16:
            _, old = _transcript.pop(0)
            total -= len(old)

    _output_scroll_ref: list[Any] = [None]

    def _sp() -> Any:
        return _output_scroll_ref[0]

    def _viewport_rows() -> int:
        try:
            app = get_app()
            return max(6, (app.output.get_size().rows or 24) - 4)
        except Exception:
            return 20

    def _viewport_cols() -> int:
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
        vh = _content_preferred_height()
        rows = _viewport_rows()
        return max(0, vh - rows)

    def _output_at_bottom() -> bool:
        sp = _sp()
        if sp is None:
            return True
        return sp.vertical_scroll >= _max_output_scroll() - 1

    def _snap_output_bottom() -> None:
        sp = _sp()
        if sp is not None:
            sp.vertical_scroll = _max_output_scroll()

    def _wheel_line_step() -> int:
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
            self._inner = inner

        def preferred_width(self, max_available_width: int) -> int | None:
            return self._inner.preferred_width(max_available_width)

        def preferred_height(
            self,
            width: int,
            max_available_height: int,
            wrap_lines: bool,
            get_line_prefix,
        ) -> int | None:
            return self._inner.preferred_height(
                width, max_available_height, wrap_lines, get_line_prefix
            )

        def create_content(self, width: int, height: int) -> UIContent:
            return self._inner.create_content(width, height)

        def mouse_handler(self, mouse_event: MouseEvent) -> NotImplementedOrNone:
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
        text=lambda: _transcript,
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
        if not text:
            return
        at_bottom = _output_at_bottom()
        if _transcript and _transcript[-1][0] == style_cls:
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
        return "".join(t for _, t in _transcript)

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
        return max(1, _viewport_rows() // 2)

    @kb.add("pageup", filter=has_focus(input_buffer))
    def _on_pageup(event):
        _stick_bottom[0] = False
        output_scroll.vertical_scroll = max(0, output_scroll.vertical_scroll - _scroll_step())
        event.app.invalidate()

    @kb.add("pagedown", filter=has_focus(input_buffer))
    def _on_pagedown(event):
        _stick_bottom[0] = False
        output_scroll.vertical_scroll = min(
            _max_output_scroll(), output_scroll.vertical_scroll + _scroll_step()
        )
        event.app.invalidate()

    @kb.add("c-home", filter=has_focus(input_buffer))
    def _on_ctrl_home(event):
        _stick_bottom[0] = False
        output_scroll.vertical_scroll = 0
        event.app.invalidate()

    @kb.add("c-end", filter=has_focus(input_buffer))
    def _on_ctrl_end(event):
        _stick_bottom[0] = True
        _snap_output_bottom()
        event.app.invalidate()

    # 无坐标的滚轮（Windows 控制台等）默认会变成 Up/Down 只作用于输入框；eager 优先改为滚动 transcript。
    @kb.add(Keys.ScrollUp, eager=True, filter=has_focus(input_buffer))
    def _on_scroll_up_key(event):
        _apply_transcript_scroll(-_wheel_line_step(), "keys.ScrollUp")
        event.app.invalidate()

    @kb.add(Keys.ScrollDown, eager=True, filter=has_focus(input_buffer))
    def _on_scroll_down_key(event):
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

    def _thinking_sink(fragment: str, kind: str = "chunk") -> None:
        style = "class:cli-think-head" if kind == "label" else "class:cli-think-body"
        _append_transcript(style, fragment)

    engine.thinking.set_output_sink(_thinking_sink)

    _cli_w = 60

    def _cli_rule_heavy() -> None:
        _append_transcript("class:cli-border-strong", "\u2550" * _cli_w + "\n")

    def _cli_rule_light() -> None:
        _append_transcript("class:cli-border", "\u2500" * _cli_w + "\n")

    def _cli_block_user(prompt: str) -> None:
        """本轮提问区块。"""
        _stick_bottom[0] = True
        _append_transcript("class:cli-spacer", "\n")
        _cli_rule_heavy()
        _append_transcript("class:cli-user-title", "You\n")
        _cli_rule_light()
        for line in (prompt or "").splitlines() or [""]:
            _append_transcript("class:cli-user-body", "  " + line + "\n")
        _append_transcript("class:cli-spacer", "\n")

    def _cli_block_reply(text: str) -> None:
        """最终回复区块。"""
        _append_transcript("class:cli-spacer", "\n")
        _cli_rule_light()
        _append_transcript("class:cli-assistant-title", "Assistant\n")
        _cli_rule_light()
        for line in (text or "").splitlines() or [""]:
            _append_transcript("class:cli-assistant-body", "  " + line + "\n")
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
            try:
                unregister_instance()
                term_write("\u2705 当前实例已停止", "ansigreen")
            except Exception:
                pass
            release_session_lock(state["active_session_id"])
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
    )
    from miniagent.engine.session_lock import (
        is_session_locked,
        release_session_lock,
        try_lock_session,
    )

    active_profile = os.environ.get("MODEL_PROFILE", "balanced")

    _fb_w = 60

    def _fb_rule_heavy() -> None:
        print("\u2550" * _fb_w)

    def _fb_rule_light() -> None:
        print("\u2500" * _fb_w)

    async def _process_input(user_input: str) -> None:
        try:
            session_key = channel_router.resolve("__cli__")
            print()
            _fb_rule_heavy()
            print("You")
            _fb_rule_light()
            for line in (user_input or "").splitlines() or [""]:
                print("  " + line)
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
            )
            print()
            _fb_rule_light()
            print("Assistant")
            _fb_rule_light()
            for line in (reply or "").splitlines() or [""]:
                print("  " + line)
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
            try:
                unregister_instance()
                print("\u2705 \u5f53\u524d\u5b9e\u4f8b\u5df2\u505c\u6b62")
            except Exception:
                pass
            release_session_lock(state["active_session_id"])
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
                print("\n\u7528\u6cd5:")
                print("  .session list                  \u5217\u51fa\u6240\u6709\u4f1a\u8bdd")
                print("  .session switch <id>           \u5207\u6362\u5230\u6307\u5b9a\u4f1a\u8bdd")
                print("  .session create <id> [title]   \u521b\u5efa\u65b0\u4f1a\u8bdd")
                print("  .session rename <id> <title>   \u91cd\u547d\u540d\u4f1a\u8bdd\n")
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
                ctx.feishu.stop()
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
            else:
                print("\n\u7528\u6cd5:")
                print("  .queue status          \u67e5\u770b\u961f\u5217\u72b6\u6001")
                print("  .queue set <mode>      \u5207\u6362\u6a21\u5f0f (queue / preemptive)")
                print(f"  \u5f53\u524d\u6a21\u5f0f: {message_queue.mode.value}\n")
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
    from miniagent.engine.command_dispatch import dispatch_command

    channel_router = ctx.channel_router
    _emit_feishu_cli = _feishu_user_status_fn(ctx)

    async def handler(
        content: str,
        chat_id: str,
        sender_id: str,
        chat_type: str = "group",
    ) -> str:
        """处理单条飞书消息。

        以 `.` 开头的消息路由到统一命令调度器（与 CLI 共享）。
        普通消息通过 ChannelRouter 解析 session_key 后交给 Agent 处理。

        Args:
            content: 消息文本内容
            chat_id: 飞书聊天室 ID
            sender_id: 消息发送者 ID
            chat_type: "group"（群聊）或 "p2p"（私聊）

        Returns:
            Agent 回复文本或错误提示。
        """
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
                    allow_session_mutations_when_capture=False,
                )
                if reply is not None:
                    _emit_feishu_cli(
                        f"\n\U0001f4e8 [\u98de\u4e66\u547d\u4ee4 {chat_id[:8]}] {content}"
                    )
                    return reply
            except Exception as e:
                return f"\u274c \u547d\u4ee4\u6267\u884c\u5931\u8d25: {e}"

        # ── 解析 session_key ──
        session_key = channel_router.resolve_feishu_message(
            chat_id, sender_id, chat_type
        )

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
                is_feishu=chat_type == "group",
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
            )
            return reply
        except Exception as e:
            return f"\u26a0\ufe0f \u5904\u7406\u5931\u8d25: {e}"

    return handler


__all__ = ["unified_main", "run_cli_loop"]
