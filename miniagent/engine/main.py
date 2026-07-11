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

.. note::
   本文件当前约 3000 行，未来重构建议拆分为：
   - cli_loop.py: 主循环逻辑（输入处理、命令分发）
   - cli_ui.py: prompt_toolkit UI 构建（补全、样式、键绑定）
   - cli_history.py: 历史加载/渲染/复制模式
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys
import threading
from collections import deque  # 性能优化：deque的popleft()为O(1)
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

# 性能优化：预编译高频正则表达式
_FILE_MARKER_PATTERN = re.compile(r"@file:([^\s]+)|file:([^\s]+)")

if TYPE_CHECKING:
    # prompt_toolkit≥3.0.50 仅在类型检查块中定义该别名，运行时 key_bindings 无此名（勿在运行中 from … import）。
    pass

from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.feishu_handler import create_feishu_handler
from miniagent.engine.shutdown import shutdown_runtime

# 飞书状态行输出（用于 feishu.start() 的 user_status 参数）
from miniagent.engine.utils import detect_mime_from_magic, get_render_width
from miniagent.engine.utils import feishu_user_status_fn as _feishu_user_status_fn
from miniagent.infrastructure.instance import (
    ProjectDirConflictError,
    format_project_conflict_message,
    heartbeat,
    register_instance,
    unregister_instance,
)
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import set_console_log_threshold
from miniagent.runtime.context import RuntimeContext
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX

_logger = logging.getLogger(__name__)


from miniagent.engine.clipboard import copy_text_to_system_clipboard


def _configure_console_encoding() -> None:
    """在 Windows 平台将 stdout/stderr 设为 UTF-8，避免中文编码异常。"""
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _resolve_cli_history_file() -> str:
    """CLI 输入历史文件路径（全屏 TUI 的 FileHistory 与 fallback readline 共用）。"""
    from miniagent.infrastructure.paths import resolve_state_dir

    history_dir = os.path.join(resolve_state_dir(), "cli")
    os.makedirs(history_dir, exist_ok=True)
    path = os.path.join(history_dir, "history.txt")
    legacy = os.path.join(os.path.expanduser("~"), ".miniagent_cli_history")
    if not os.path.isfile(path) and os.path.isfile(legacy):
        try:
            import shutil

            shutil.copy2(legacy, path)
        except Exception as e:
            _logger.debug("迁移旧 CLI 历史 (%s → %s) 失败: %s", legacy, path, e)
    return path


def _create_cli_file_history(filename: str) -> Any:
    """创建 CLI 输入历史后端（``FileHistory`` 子类，写入前确保目录存在）。"""
    from prompt_toolkit.history import FileHistory

    class SafeFileHistory(FileHistory):
        """FileHistory 的安全版本，每次写入前确保父目录存在。"""

        def store_string(self, string: str) -> None:
            parent_dir = os.path.dirname(self.filename)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            super().store_string(string)

        def merge_strings_memory_only(self, strings: Iterable[str]) -> None:
            """将字符串并入上下键导航历史，不写入 ``history.txt``。"""
            if not self._loaded:
                self._loaded_strings = list(self.load_history_strings())
                self._loaded = True
            known = {(x or "").strip() for x in self._loaded_strings if (x or "").strip()}
            for raw in reversed(list(strings)):
                s = (raw or "").strip()
                if not s or s in known:
                    continue
                self._loaded_strings.insert(0, s)
                known.add(s)

    return SafeFileHistory(filename)


def _cli_input_history_max() -> int:
    """CLI 输入框 ↑↓ 回顾的会话 user 消息条数上限。"""
    return max(1, int(get_config("cli.input_history_max", 100)))


def _session_user_inputs_for_cli_history(state: dict, *, limit: int | None = None) -> list[str]:
    """收集当前会话中可用于 CLI 输入回顾的 user 消息（时间正序，可限条数）。"""
    sm = state.get("session_manager")
    if sm is None:
        return []
    session_id = state.get("active_session_id", "")
    if not session_id:
        return []
    session = sm.get(session_id)
    if session is None:
        return []

    from miniagent.engine.cli_commands import _load_session_history_messages

    messages = _load_session_history_messages(session)
    result: list[str] = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = (msg.get("content") or "").strip()
            if content:
                result.append(content)
    max_items = limit if limit is not None else _cli_input_history_max()
    if len(result) > max_items:
        result = result[-max_items:]
    return result


def _prime_cli_input_history_from_session(state: dict, buf: Any, *, limit: int | None = None) -> None:
    """将当前会话 user 消息并入内存输入历史（不污染 ``history.txt``）。"""
    hist = getattr(buf, "history", None)
    merge = getattr(hist, "merge_strings_memory_only", None)
    if merge is None:
        return
    strings = _session_user_inputs_for_cli_history(state, limit=limit)
    if not strings:
        return
    try:
        merge(strings)
    except Exception as e:
        _logger.warning("历史加载失败，继续启动: %s", e)


class _HistoryLoadDone:
    """无运行中事件循环时，代替已完成 asyncio.Task 的占位。"""

    def done(self) -> bool:
        return True

    def result(self) -> None:
        return None


def _mark_buffer_history_preloaded(buf: Any) -> None:
    """标记 Buffer 历史已同步加载，避免 prompt_toolkit 重复启动异步 load 任务。"""
    if getattr(buf, "_load_history_task", None) is not None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        buf._load_history_task = _HistoryLoadDone()  # type: ignore[assignment]
        return
    fut = loop.create_future()
    fut.set_result(None)
    buf._load_history_task = fut


def _sync_preload_buffer_working_lines(buf: Any) -> None:
    """同步将 FileHistory 条目填入 Buffer._working_lines（修复首次按 ↑ 无反应）。"""
    hist = getattr(buf, "history", None)
    if hist is None or not hasattr(hist, "get_strings"):
        return
    strings = list(hist.get_strings())
    current = buf.text
    buf._working_lines.clear()
    for s in strings:
        buf._working_lines.append(s)
    buf._working_lines.append(current)
    buf.working_index = len(buf._working_lines) - 1
    _mark_buffer_history_preloaded(buf)


def _reload_cli_input_history(
    state: dict,
    buf: Any,
    history_file: str,
    *,
    limit: int | None = None,
) -> None:
    """重建输入历史后端、合并会话 user 消息并同步预填充 Buffer。"""
    buf.history = _create_cli_file_history(history_file)
    _prime_cli_input_history_from_session(state, buf, limit=limit)
    _sync_preload_buffer_working_lines(buf)


def _prime_fallback_readline_history(history_file: str) -> None:
    """Fallback 模式：将 history.txt 最近条目写入 readline（若可用）。"""
    try:
        import readline
    except ImportError:
        return
    if not os.path.isfile(history_file):
        return
    try:
        lines: list[str] = []
        with open(history_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("+") and len(line) > 1:
                    lines.append(line[1:])
        for entry in lines[-_cli_input_history_max() :]:
            readline.add_history(entry)
    except Exception as e:
        _logger.debug("fallback readline 历史预填充失败: %s", e)


_FileNotifyFn = Callable[[str, str], None]


async def detect_and_process_file_markers(
    user_input: str,
    session_key: str,
    session_manager: Any,
    runtime_ctx: Any,
    *,
    notify: _FileNotifyFn | None = None,
) -> tuple[str, list[dict]]:
    """检测用户输入中的 ``@file:`` / ``file:`` 标记，处理文件并写入记忆。

    Args:
        user_input: 用户原始输入
        session_key: 会话 ID
        session_manager: 会话管理器（可为 None，此时仅按 cwd 解析路径）
        runtime_ctx: 运行时上下文
        notify: 可选 ``(message, color)`` 回调；color 为 ``ansicyan`` / ``ansiyellow`` 等

    Returns:
        (替换标记后的输入, 已处理文件信息列表)
    """
    files_info: list[dict] = []

    matches = _FILE_MARKER_PATTERN.findall(user_input)
    if not matches:
        return user_input, files_info

    for match in matches:
        file_path = match[0] or match[1]
        if not file_path:
            continue

        try:
            base_path = ""
            if session_manager:
                session = session_manager.get(session_key)
                if session:
                    base_path = session.workspace_path or ""

            if not os.path.isabs(file_path):
                if os.path.exists(file_path):
                    resolved = file_path
                elif base_path and os.path.exists(os.path.join(base_path, file_path)):
                    resolved = os.path.join(base_path, file_path)
                else:
                    resolved = file_path
            else:
                resolved = file_path

            if not os.path.isfile(resolved):
                if notify:
                    notify(f"{WARNING_PREFIX} 文件不存在: {file_path}\n", "ansiyellow")
                continue

            file_name = os.path.basename(resolved)
            file_size = os.path.getsize(resolved)

            try:
                with open(resolved, "rb") as f:
                    header = f.read(32)
                mime_type = detect_mime_from_magic(header) or "application/octet-stream"
            except Exception as e:
                _logger.debug("读取文件 MIME 失败 (%s): %s", resolved, e)
                mime_type = "application/octet-stream"

            if mime_type.startswith("image/"):
                file_type = "image"
            elif mime_type.startswith("text/"):
                file_type = "text"
            else:
                file_type = "binary"

            description = ""
            vision_desc_enabled = get_config("cli.file_vision_desc", True)
            if file_type == "image" and runtime_ctx and vision_desc_enabled:
                try:
                    from miniagent.feishu.vision_desc import describe_image

                    client = getattr(runtime_ctx, "openai_client", None)
                    model = get_config("model.model", "gpt-4o-mini")
                    if client:
                        description = await describe_image(resolved, client, model)
                except Exception as e:
                    _logger.debug("图片描述生成失败 (%s): %s", resolved, e)
            elif file_type == "text":
                try:
                    with open(resolved, encoding="utf-8", errors="ignore") as f:
                        preview = f.read(500)
                    description = preview[:200]
                except Exception as e:
                    _logger.debug("文本文件预览失败 (%s): %s", resolved, e)

            try:
                from miniagent.memory.store import add_file_to_memory
                from miniagent.types.memory import FileMetadata

                rel_path = file_path if not os.path.isabs(file_path) else os.path.basename(resolved)

                file_meta = FileMetadata(
                    name=file_name,
                    path=rel_path,
                    size=file_size,
                    mime_type=mime_type,
                    type=file_type,
                    description=description,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="cli",
                )

                await add_file_to_memory(
                    session_key,
                    file_meta,
                    getattr(runtime_ctx, "memory_store", None),
                )

                files_info.append({
                    "name": file_name,
                    "type": file_type,
                    "size": file_size,
                    "description": description[:100] if description else "",
                })

                marker = f"@file:{file_path}" if match[0] else f"file:{file_path}"
                type_label = {"image": "图片", "text": "文本文件", "binary": "文件"}.get(
                    file_type, "文件"
                )
                max_desc_len = 150 if file_type == "image" else 100
                if description:
                    truncated_desc = description[:max_desc_len]
                    content_label = "图片内容" if file_type == "image" else "内容预览"
                    replacement = f"[{type_label}: {file_name}]\n{content_label}：{truncated_desc}"
                else:
                    replacement = f"[{type_label}: {file_name}]"
                user_input = user_input.replace(marker, replacement)

                if notify:
                    size_kb = file_size // 1024 if file_size >= 1024 else file_size
                    size_label = f"{size_kb}KB" if file_size >= 1024 else f"{size_kb}B"
                    notify(f"📎 已处理文件: {file_name} ({size_label})\n", "ansicyan")
                    if description:
                        suffix = "..." if len(description) > 100 else ""
                        notify(
                            f"   内容摘要: {description[:100]}{suffix}\n",
                            "ansicyan",
                        )
            except Exception as e:
                _logger.warning("文件标记写入记忆失败 (%s): %s", file_path, e)
                if notify:
                    notify(f"{WARNING_PREFIX} 无法保存文件到记忆: {file_name}\n", "ansiyellow")
        except Exception as e:
            _logger.warning("处理文件标记失败 (%s): %s", file_path, e)
            if notify:
                notify(f"{WARNING_PREFIX} 处理文件失败: {e}\n", "ansiyellow")

    return user_input, files_info


def run_cli_bash_command(bash_cmd: str) -> tuple[bool, str]:
    """执行 ``!`` 前缀 Bash 命令。

    Returns:
        ``(ok, 格式化输出)``。``ok`` 为 True 仅当子进程退出码为 0；
        超时或启动/执行异常时 ``ok`` 为 False（输出仍含 stderr/退出码说明）。
    """
    import subprocess

    from miniagent.core.constants import CLI_BASH_TIMEOUT

    timeout = CLI_BASH_TIMEOUT
    try:
        result = subprocess.run(
            bash_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output_lines = [f"⚙️ Bash: {bash_cmd}"]
        if result.stdout:
            output_lines.append(result.stdout)
        if result.stderr:
            output_lines.append(f"{ERROR_PREFIX} stderr: {result.stderr}")
        if result.returncode != 0:
            output_lines.append(f"退出码: {result.returncode}")
        return result.returncode == 0, "\n".join(output_lines) + "\n"
    except subprocess.TimeoutExpired:
        return False, f"{ERROR_PREFIX} Bash超时（{timeout}s）: {bash_cmd}\n"
    except Exception as e:
        return False, f"{ERROR_PREFIX} Bash错误: {e}\n"


# ─── unified_main：RuntimeContext 注入后的进程主流程（init → 信号/实例 → CLI 循环 / 飞书任务）──


async def unified_main(ctx: RuntimeContext) -> None:
    """主启动流程。

    不再检查全局单实例 — 支持多实例并行。
    每个实例通过会话级 .lock 文件隔离。

    嵌入场景若不经 ``compat.unified_entry``，调用方须先
    ``load_secrets_from_project_root()`` 或预先设置 ``OPENAI_*`` 等敏感凭据环境变量。

    Args:
        ctx: 运行时组合根（registry / monitor / skill_registry / clawhub / engine）
    """
    registry = ctx.registry
    skill_registry = ctx.skill_registry
    engine = ctx.engine
    _configure_console_encoding()

    # ── 用户体验增强：配置热更新 ──
    from miniagent.infrastructure.config_watch import start_config_watch
    start_config_watch(ctx)

    # 尝试启用 Windows VT 模式（某些终端可能不支持）
    try:
        import ctypes

        _h = ctypes.windll.kernel32.GetStdHandle(-11)
        if _h and _h != -1:
            _mode = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetConsoleMode(_h, ctypes.byref(_mode)):
                _new_mode = _mode.value | 0x0004
                ctypes.windll.kernel32.SetConsoleMode(_h, _new_mode)
    except Exception as e:
        _logger.debug("Windows VT模式设置失败（降级到prompt_toolkit）: %s", e)  # VT 模式不可用，降级到 prompt_toolkit 颜色

    MODEL = get_config("model.model", "gpt-4o-mini")
    from miniagent.engine.init import init_subsystems
    from miniagent.engine.welcome import print_welcome

    # 磁盘注册：分配 instance_id 前会清扫 PID 已失效的目录（不 kill 其它进程）
    feishu_mode = "--feishu" in sys.argv

    try:
        reg_result = register_instance(
            mode="both" if feishu_mode else "cli",
            active_sessions=[],
        )
    except ProjectDirConflictError as e:
        print(format_project_conflict_message(e.existing_meta))
        raise SystemExit(2) from e
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
    _dummy_stick: list[bool] = [True]
    ctx.create_feishu_handler_factory = lambda st: create_feishu_handler(st, ctx, _dummy_stick)

    # 信号：在事件循环线程内 await 统一关停（飞书 WS reset、子进程、实例注销）
    main_loop = asyncio.get_running_loop()
    _sig_lock = threading.Lock()
    _sig_armed: dict[str, bool] = {"v": False}

    async def _shutdown_after_signal(signum: int) -> None:
        """信号触发后在事件循环内执行 ``shutdown_runtime`` 并退出进程。

        使用 os._exit(0) 而非 sys.exit(0) 以避免 SystemExit 异常未被捕获。
        """
        try:
            await shutdown_runtime(
                ctx,
                state,
                reason=f"signal:{signum}",
                call_unregister=True,
            )
        except Exception as e:
            _logger.debug("信号关闭过程中异常（不影响退出）: %s", e)  # 关闭过程中的异常不影响最终退出
        # 使用 os._exit 直接终止进程，避免 SystemExit 异常
        os._exit(0)

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

    (
        loaded_skills,
        skill_toolboxes,
        skill_prompts,
        active_session_id,
        session_manager,
    ) = await init_subsystems(
        registry,
        skill_registry,
        engine,
        SessionManager,
        ctx.channel_router,
        clawhub=ctx.clawhub,
        keyword_index=ctx.keyword_index,
    )
    state["active_session_id"] = active_session_id
    state["skill_toolboxes"] = skill_toolboxes
    state["skill_prompts"] = skill_prompts
    state["session_manager"] = session_manager

    from miniagent.engine.parallel_config import configure_message_queue_for_parallel

    configure_message_queue_for_parallel(ctx.message_queue)
    engine.set_active_session_key(active_session_id)

    # 飞书与 CLI 共进程：先起 WS 长轮询任务，再进入同一 stdin 主循环（无单独纯飞书入口）
    if state["feishu_enabled"]:
        ctx.feishu.start(
            ctx.create_feishu_handler_factory,
            state,
            user_status=_feishu_user_status_fn(ctx),
        )

    from miniagent.scheduled_tasks.ticker import start_scheduled_tasks_ticker

    start_scheduled_tasks_ticker(ctx, state, skill_toolboxes, skill_prompts)

    from miniagent.skills.watch import start_skills_watch

    start_skills_watch(registry, skill_registry, state, ctx)

    # 显示欢迎信息
    print_welcome(
        registry,
        skill_registry,
        MODEL,
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
    )


# ─── run_cli_loop：prompt_toolkit 全屏/简化终端上的 stdin 主循环（点命令 → 队列 → Agent）──


async def run_cli_loop(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list,
    skill_prompts: list,
) -> None:
    """CLI 交互循环（使用 prompt_toolkit 实现固定输入区）。

    ``skill_toolboxes`` / ``skill_prompts`` 参数作为 fallback；优先从 ``state`` 读取以支持热加载。

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

    from miniagent.skills.snapshots import (
        get_skill_prompts_from_state,
        get_skill_toolboxes_from_state,
        join_skill_prompts,
    )

    def _skill_tb() -> list:
        """从 state 获取当前技能工具箱列表（fallback 到传入参数）。"""
        return get_skill_toolboxes_from_state(state) or skill_toolboxes

    def _skill_sp() -> str | None:
        """从 state 获取当前技能提示词拼接字符串（fallback 到传入参数）。"""
        return join_skill_prompts(get_skill_prompts_from_state(state) or skill_prompts)

    try:
        # 用户体验增强：Tab 自动补全
        from prompt_toolkit.completion import Completer, Completion, merge_completers
        from prompt_toolkit.completion import PathCompleter as PTPathCompleter
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.styles import Style
    except ImportError:
        await _run_cli_loop_fallback(
            ctx,
            state,
            skill_toolboxes,
            skill_prompts,
        )
        return

    # Linux 兼容性：cli.force_fallback=true 时跳过全屏模式，直接使用 input() 循环
    if get_config("cli.force_fallback", False):
        await _run_cli_loop_fallback(
            ctx,
            state,
            skill_toolboxes,
            skill_prompts,
        )
        return

    # 无 TTY（如 pytest 子进程重定向 stdin/stdout）时全屏 Application 无法初始化，回退到 input() 循环
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        await _run_cli_loop_fallback(
            ctx,
            state,
            skill_toolboxes,
            skill_prompts,
        )
        return

    # 历史记录文件（与 fallback readline 共用路径）
    history_file = _resolve_cli_history_file()

    # ── 用户体验增强：Tab 自动补全 ──
    class CommandCompleter(Completer):
        """斜杠命令补全器（用户体验增强）。"""

        def get_completions(self, document, complete_event):
            """在 ``/`` 前缀输入时补全已注册点命令。"""
            text = document.text_before_cursor
            if text.startswith("/") and len(text) >= 1:
                # 获取输入的命令部分（不含参数）
                parts = text.split()
                if parts:
                    cmd_prefix = parts[0].lower()
                    # 从 command_dispatch 获取已注册命令列表
                    from miniagent.engine.command_dispatch import _REGISTERED_COMMANDS
                    for cmd in _REGISTERED_COMMANDS:
                        if cmd.lower().startswith(cmd_prefix):
                            # 计算需要替换的位置
                            yield Completion(
                                cmd,
                                start_position=-len(cmd_prefix),
                                display=cmd,
                                display_meta="命令",
                            )

    class FilePathCompleter(Completer):
        """@file: 标记的文件路径补全器（用户体验增强）。"""

        def get_completions(self, document, complete_event):
            """在 ``@file:`` / ``file:`` 前缀后补全文件路径。"""
            text = document.text_before_cursor
            match = re.search(r"(@file:|file:)([^\s]*)$", text)
            if not match:
                return
            partial_path = match.group(2)
            try:
                from prompt_toolkit.document import Document

                path_completer = PTPathCompleter()
                path_doc = Document(partial_path, cursor_position=len(partial_path))
                for completion in path_completer.get_completions(path_doc, complete_event):
                    yield Completion(
                        completion.text,
                        start_position=-len(partial_path),
                        display=completion.display,
                        display_meta="文件",
                    )
            except Exception as e:
                _logger.debug("文件路径补全失败: %s", e)

    # 创建合并补全器
    command_completer = CommandCompleter()
    file_completer = FilePathCompleter()
    merged_completer = merge_completers([command_completer, file_completer])

    # ── CLI 界面：底部固定输入框（类似 Claude Code） ──
    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import Condition, has_focus
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import (
        BufferControl,
        FormattedTextControl,
        UIContent,
        UIControl,
    )
    from prompt_toolkit.layout.dimension import LayoutDimension as D
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.layout.scrollable_pane import ScrollablePane
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

    from miniagent.engine.session_lock import release_session_lock

    input_buffer = Buffer(
        history=_create_cli_file_history(history_file),
        completer=merged_completer,  # 用户体验增强：Tab 自动补全
        complete_while_typing=False,  # 仅在 Tab 键触发补全
    )

    # 启动时合并会话 user 消息到内存导航历史（不写入 history.txt）
    _reload_cli_input_history(state, input_buffer, history_file)

    # 使用 constants.py 的裁剪阈值，避免硬编码导致阈值过低
    from miniagent.core.constants import MAX_TRANSCRIPT_CHARS

    _MAX_TRANSCRIPT_CHARS = int(get_config("memory.max_transcript_chars", MAX_TRANSCRIPT_CHARS))
    _transcript: deque[Any] = deque()  # 仅由 _MAX_TRANSCRIPT_CHARS 显式控制裁剪
    _transcript_total_len: list[int] = [0]  # 累计长度计数器（性能优化）
    _stick_bottom: list[bool] = [True]
    _last_md_width: list[int] = [0]  # 上次渲染 Markdown 的终端宽度

    # ─── 流式思考输出累加器（按 session_key 隔离）────────────────
    from dataclasses import dataclass

    @dataclass
    class _StreamingThinkState:
        active: bool = False
        text: str = ""
        start_idx: int = -1

    _streaming_think_by_session: dict[str, _StreamingThinkState] = {}

    def _stream_state(session_key: str = "") -> _StreamingThinkState:
        """按 session_key 获取或创建流式思考渲染状态。"""
        sk = (session_key or "").strip() or "default"
        if sk not in _streaming_think_by_session:
            _streaming_think_by_session[sk] = _StreamingThinkState()
        return _streaming_think_by_session[sk]

    # ─── 复制模式状态 ─────────────────────────────────────────────
    _copy_mode_active: list[bool] = [False]  # 复制模式激活状态
    _selection_start: list[tuple[int, int] | None] = [None]  # (fragment索引, 字符偏移)
    _selection_end: list[tuple[int, int] | None] = [None]  # (fragment索引, 字符偏移)
    _selection_text: list[str] = [""]  # 选中内容缓存
    _copy_mode_mouse_down: list[bool] = [False]  # 鼠标按下状态（用于拖动选择）

    # 历史记录渐进式加载状态
    _history_loaded_range: dict[str, Any] = {
        "total_messages": 0,
        "loaded_start": 0,
        "loaded_end": 0,
        "batch_size": 3,
        "all_loaded": False,
        "loading": False,
    }
    _initial_history_count: int = int(get_config("memory.initial_history_count", 5))

    from miniagent.engine.cli_transcript import (
        is_valid_pt_style as _is_valid_pt_style,
    )
    from miniagent.engine.cli_transcript import (
        markdown_render_width as _markdown_render_width_for_vp,
    )
    from miniagent.engine.cli_transcript import (
        rule_line_width as _rule_line_width_for_vp,
    )
    from miniagent.engine.cli_transcript import (
        safe_ansi_fragments as _safe_ansi,
    )

    def _transcript_fragment_len(frag: Any) -> int:
        """估算单条 transcript 片段的字符长度（tuple 文本或 ``ANSI`` 包裹串）。"""
        from miniagent.engine.cli_transcript import transcript_fragment_len

        return transcript_fragment_len(frag)

    def _trim_transcript() -> None:
        """性能优化：使用累计长度计数器，避免每次遍历（O(1)而非O(n))。

        使用deque的popleft()操作，性能从O(n)提升到O(1)。
        """
        # 边界检查：确保计数器不为负数，防止无限循环
        while (
            _transcript_total_len[0] > _MAX_TRANSCRIPT_CHARS
            and len(_transcript) > 16
            and _transcript_total_len[0] >= 0
        ):
            old = _transcript.popleft()  # O(1)操作（deque性能优化）
            frag_len = _transcript_fragment_len(old)
            # 防止计数器减到负数
            _transcript_total_len[0] = max(0, _transcript_total_len[0] - frag_len)

    def _transcript_prepend(style: Any, text: str) -> None:
        """在 transcript 顶部插入内容，并维护字符长度裁剪。"""
        _transcript.insert(0, (style, text))
        _transcript_total_len[0] += len(text)
        _trim_transcript()

    def _render_history_message_to_transcript(
        msg: dict,
        prepend: bool = False,
        *,
        plain_text: bool = False,
    ) -> None:
        """将历史消息渲染到 transcript。

        Args:
            msg: 历史消息字典，包含 role 和 content
            prepend: True 时插入到顶部（加载更旧历史），False 时追加到底部（初始加载）
            plain_text: True 时 assistant 跳过 Markdown 渲染（启动加速）
        """

        from miniagent.engine.markdown_cli import render_markdown_to_ansi

        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            return
        from miniagent.engine.cli_transcript import lines_for_prepend, rule_line_width

        vp = _viewport_cols()
        rule_w = rule_line_width(vp)

        if role == "user":
            if prepend:
                # prepend=True: 插入到顶部，每条消息内部按正确顺序插入
                # insert(0) 后插入的在上面，所以要：先插内容，再插标题分隔线
                # 最终显示：spacer → 上分隔线 → 标题 → 内容 → 下分隔线
                for line in lines_for_prepend(content):
                    _transcript_prepend("class:cli-user-body", line + "\n")
                _transcript_prepend("class:cli-user-title", "You\n")
                _transcript_prepend("class:cli-border", "─" * rule_w + "\n")
                _transcript_prepend("class:cli-border-strong", "═" * rule_w + "\n")
                _transcript_prepend("class:cli-spacer", "\n")
            else:
                _cli_block_user(content)
        elif role == "assistant":
            if plain_text and not prepend:
                _append_transcript("class:cli-assistant-title", "Assistant\n")
                for line in (content or "").splitlines() or [content]:
                    _append_transcript("class:cli-assistant-body", line + "\n")
                _append_transcript("class:cli-border", "─" * rule_w + "\n")
            elif prepend:
                md_w = _markdown_render_width()
                ansi = render_markdown_to_ansi(content, width=md_w, justify="left")
                # prepend=True: 插入到顶部，顺序：标题 → 内容 → 分隔线
                if ansi:
                    # 使用安全的 ANSI 处理
                    safe_ft = _safe_ansi(ansi)
                    # 反向插入（prepend）
                    for style, txt in reversed(safe_ft):
                        _transcript_prepend(style, txt)
                else:
                    for line in lines_for_prepend(content):
                        _transcript_prepend("class:cli-assistant-body", line + "\n")
                _transcript_prepend("class:cli-assistant-title", "Assistant\n")
                _transcript_prepend("class:cli-border", "─" * rule_w + "\n")
            else:
                _cli_block_reply(content)
        elif role == "thinking":
            if prepend:
                _transcript_prepend("class:cli-think-head", "💭 Thinking\n")
                _transcript_prepend("class:cli-spacer", "\n")
            else:
                _append_transcript("class:cli-think-head", "💭 Thinking\n")

    def _load_initial_history_to_transcript() -> None:
        """加载最近几条历史到 transcript 显示区。"""
        sm = state.get("session_manager")
        if not sm:
            _logger.warning("历史加载失败: session_manager 未设置")
            return
        session_id = state.get("active_session_id", "")
        if not session_id:
            _logger.warning("历史加载失败: active_session_id 未设置")
            return

        try:
            messages, total = sm.load_session_history_range(
                session_id,
                start_idx=0,
                count=_initial_history_count,
            )
            _logger.info(f"历史加载: session={session_id}, total={total}, loaded={len(messages)}")

            # 无论是否有历史，都设置状态
            _history_loaded_range["total_messages"] = total
            _history_loaded_range["loaded_start"] = 0
            # 使用实际加载的消息数量，而非请求的 count（修复后可能多1条）
            from miniagent.engine.cli_transcript import history_all_loaded, history_loaded_end

            _history_loaded_range["loaded_end"] = history_loaded_end(0, len(messages), total)
            # 如果实际加载数量 >= total，则全部已加载
            _history_loaded_range["all_loaded"] = history_all_loaded(
                total,
                _history_loaded_range["loaded_end"],
            )

            if not messages:
                _logger.info("历史加载: 无消息，跳过渲染")
                return

            # 渲染历史到 transcript（从旧到新；启动批次用纯文本加速）
            for msg in messages:
                _render_history_message_to_transcript(msg, prepend=False, plain_text=True)
            _logger.info(f"历史加载: 已渲染 {len(messages)} 条消息到 transcript")

            # 如果有更多历史，添加提示
            if not _history_loaded_range["all_loaded"]:
                from miniagent.engine.cli_transcript import (
                    HISTORY_HINT_STYLE,
                    history_load_hint,
                    history_remaining,
                )

                remaining = history_remaining(total, _history_loaded_range["loaded_end"])
                _transcript_prepend(HISTORY_HINT_STYLE, history_load_hint(remaining))
        except Exception as e:
            _logger.exception(f"历史加载异常: {e}")

    def _reset_and_reload_transcript(*, reset_scroll_to_top: bool = False) -> None:
        """清空 transcript 并重新加载当前会话历史（启动 / 切换会话 / Ctrl+L）。"""
        _transcript.clear()
        _transcript_total_len[0] = 0
        _history_loaded_range["total_messages"] = 0
        _history_loaded_range["loaded_start"] = 0
        _history_loaded_range["loaded_end"] = 0
        _history_loaded_range["all_loaded"] = False
        _history_loaded_range["loading"] = False

        if reset_scroll_to_top:
            sp = _sp()
            if sp is not None:
                sp.vertical_scroll = 0
            _reset_horizontal_scroll()

        _load_initial_history_to_transcript()
        _stick_bottom[0] = True
        try:
            _snap_output_bottom()
            from prompt_toolkit.application import get_app

            app = get_app()
            if getattr(app, "is_running", False):
                app.invalidate()
        except Exception:
            pass

    def _trigger_lazy_load_more_history() -> None:
        """触发懒加载更多历史（防重入）。"""
        if _history_loaded_range["loading"]:
            return
        if _history_loaded_range["all_loaded"]:
            return

        _history_loaded_range["loading"] = True

        try:
            sm = state.get("session_manager")
            session_id = state.get("active_session_id", "")
            if not sm or not session_id:
                return

            next_start = _history_loaded_range["loaded_end"]
            batch = _history_loaded_range["batch_size"]

            messages, total = sm.load_session_history_range(
                session_id,
                start_idx=next_start,
                count=batch,
            )

            if not messages:
                _history_loaded_range["all_loaded"] = True
                return

            # 移除顶部的提示文字
            if _transcript and isinstance(_transcript[0], tuple):
                first_text = _transcript[0][1] if len(_transcript[0]) >= 2 else ""
                if "加载更多历史" in first_text:
                    old = _transcript.popleft()  # O(1)操作（deque性能优化）
                    # 性能优化：更新累计长度计数器
                    _transcript_total_len[0] -= _transcript_fragment_len(old)

            from miniagent.engine.cli_transcript import (
                HISTORY_HINT_STYLE,
                history_all_loaded,
                history_load_hint,
                history_loaded_end,
                history_remaining,
                messages_for_prepend,
            )

            # 在顶部插入新加载的历史。每个消息渲染函数都会多次 left-prepend，
            # 因此批次本身也要倒序遍历，最终屏幕顺序才保持从旧到新。
            for msg in messages_for_prepend(messages):
                _render_history_message_to_transcript(msg, prepend=True)

            # 更新加载范围
            _history_loaded_range["loaded_end"] = history_loaded_end(
                next_start,
                len(messages),
                total,
            )
            _history_loaded_range["all_loaded"] = history_all_loaded(
                total,
                _history_loaded_range["loaded_end"],
            )

            # 如果仍有更多，恢复提示
            if not _history_loaded_range["all_loaded"]:
                remaining = history_remaining(total, _history_loaded_range["loaded_end"])
                _transcript_prepend(
                    HISTORY_HINT_STYLE,
                    history_load_hint(remaining),
                )

            # 刷新显示
            from prompt_toolkit.application import get_app
            get_app().invalidate()

        finally:
            _history_loaded_range["loading"] = False

    def _attach_md_source(ansi_obj: Any, source_md: str) -> None:
        """在 ANSI 对象上附加原始 Markdown，供终端缩放时重新渲染。"""
        ansi_obj._source_md = source_md  # type: ignore[attr-defined]

    def _recheck_md_width() -> None:
        """检测终端宽度变化，必要时重新渲染 transcript 中的 Markdown 条目。
        同时检测是否需要切换折行/水平滚动模式。
        """
        try:
            new_w = _viewport_cols()
        except Exception:
            return
        old_w = _last_md_width[0]
        if old_w != 0 and new_w == old_w:
            return  # 宽度未变化，跳过重渲染
        _last_md_width[0] = new_w

        # ─── 水平滚动模式切换 ───────────────────────────────────────────
        # 如果宽度足够（切换到折行模式），重置水平滚动
        if _should_wrap_lines():
            _reset_horizontal_scroll()

        if not _transcript:
            return  # transcript 为空，无需重渲染
        from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI

        md_w = _markdown_render_width()  # 统一使用更宽的渲染宽度
        from miniagent.engine.markdown_cli import render_markdown_to_ansi

        for frag in _transcript:
            if isinstance(frag, PTANSI) and hasattr(frag, "_source_md"):
                src = frag._source_md
                if not src:
                    continue
                new_ansi = render_markdown_to_ansi(src, width=md_w, justify="left")
                if new_ansi is not None:
                    frag.value = new_ansi  # 更新内部 ANSI 字符串
        try:
            get_app().invalidate()
        except Exception:
            pass

    _BORDER_CLASSES = frozenset({"class:cli-border", "class:cli-border-strong"})
    _HRULE_CHARS = frozenset({"─", "═", "━"})  # ─ ═ ━ (Markdown 水平线用到的字符)

    def _is_hrule_line(text: str) -> bool:
        """判断文本是否为水平分割线（≥80% 为盒绘制字符）。"""
        if not text:
            return False
        hrule_count = sum(1 for ch in text if ch in _HRULE_CHARS)
        return hrule_count >= len(text) * 0.8

    def _truncate_hrule_in_ansi(ansi_list: list[Any], vp: int) -> list[Any]:
        """截断 ANSI 输出中的水平分割线，同时过滤无效样式（防止 emoji 解析错误）。

        ``to_formatted_text(ANSI(...))`` 返回的列表可能包含长分割线，
        使用 vp // 2 截断（与 _border_truncate 一致）。

        **安全过滤**：prompt_toolkit ANSI 解析器可能将某些字符错误解析为样式字符串，
        导致 emoji 等字符被当作颜色值，引发 'Wrong color format' 错误。
        此函数对所有 fragment 的样式进行验证，无效样式替换为空字符串。
        """
        safe = max(1, vp // 2)  # 与 _border_truncate 保持一致
        result: list[Any] = []
        for item in ansi_list:
            if isinstance(item, tuple) and len(item) >= 2:
                style, text = item[0], item[1]
                # 安全过滤：验证样式有效性
                if not _is_valid_pt_style(style):
                    style = ""  # 无效样式替换为空
                if _is_hrule_line(text.rstrip("\n")):
                    # 截断分割线
                    truncated = text[:safe]
                    if text.endswith("\n"):
                        truncated = truncated.rstrip("\n") + "\n"
                    result.append((style, truncated))
                else:
                    result.append((style, text))
            else:
                result.append(item)
        return result

    def _border_truncate(text: str, vp: int) -> str:
        """按视口宽度截断边框线文本，保留尾部 ``\n``。

        盒绘制字符（═ U+2550、─ U+2500）在 UTF-8 终端宽度约 1 列，
        使用 vp // 2 作为更合理的宽度估计（比 vp//3 更宽）。
        """
        # 安全字符数 = 视口列数 // 2（更合理的宽度估计）
        safe = max(1, vp // 2)
        has_newline = text.endswith("\n")
        if len(text) <= safe + 1:  # 已经足够短（safe chars + \n）
            return text
        truncated = text[:safe]
        if has_newline:
            truncated = truncated.rstrip("\n") + "\n"
        return truncated

    # ─── 复制模式选择辅助函数 ───────────────────────────────────────────
    def _get_transcript_fragment_text(frag_idx: int) -> str:
        """获取指定 transcript fragment 的纯文本（去除ANSI）。"""
        if frag_idx < 0 or frag_idx >= len(_transcript):
            return ""
        from miniagent.engine.cli_transcript import transcript_fragment_text

        return transcript_fragment_text(_transcript[frag_idx])

    def _get_transcript_char_count(frag_idx: int) -> int:
        """获取指定 fragment 的字符数。"""
        return len(_get_transcript_fragment_text(frag_idx))

    def _extract_selection_text() -> str:
        """根据选择范围提取纯文本。"""
        start = _selection_start[0]
        end = _selection_end[0]
        if start is None or end is None:
            return ""

        # 确保起点 <= 终点（可能反向选择）
        if start[0] > end[0] or (start[0] == end[0] and start[1] > end[1]):
            start, end = end, start

        result_parts: list[str] = []
        for frag_idx in range(start[0], end[0] + 1):
            frag_text = _get_transcript_fragment_text(frag_idx)
            if frag_idx == start[0] and frag_idx == end[0]:
                # 同一 fragment：截取范围
                result_parts.append(frag_text[start[1]:end[1]])
            elif frag_idx == start[0]:
                # 起点 fragment：从起点截取到末尾
                result_parts.append(frag_text[start[1]:])
            elif frag_idx == end[0]:
                # 终点 fragment：从开头截取到终点
                result_parts.append(frag_text[:end[1]])
            else:
                # 中间 fragment：完整内容
                result_parts.append(frag_text)

        return "".join(result_parts)

    def _clear_selection() -> None:
        """清除选择状态。"""
        _selection_start[0] = None
        _selection_end[0] = None
        _selection_text[0] = ""
        _copy_mode_mouse_down[0] = False

    def _toggle_copy_mode() -> None:
        """切换复制模式。"""
        _copy_mode_active[0] = not _copy_mode_active[0]
        if not _copy_mode_active[0]:
            # 退出复制模式时清除选择
            _clear_selection()
        try:
            get_app().invalidate()
        except Exception:
            pass

    def _apply_selection_highlight(frag_idx: int, text: str) -> list[tuple[str, str]]:
        """将文本按选择范围分割并应用高亮样式。

        Args:
            frag_idx: transcript fragment 索引
            text: 原始文本

        Returns:
            带样式的 (style, text) 元组列表
        """
        start = _selection_start[0]
        end = _selection_end[0]

        if start is None or end is None:
            return [("class:cli-default", text)]

        # 确保起点 <= 终点
        if start[0] > end[0] or (start[0] == end[0] and start[1] > end[1]):
            start, end = end, start

        # 检查当前 fragment 是否在选择范围内
        if frag_idx < start[0] or frag_idx > end[0]:
            return [("class:cli-default", text)]

        result: list[tuple[str, str]] = []

        if frag_idx == start[0] and frag_idx == end[0]:
            # 同一 fragment：前段普通 + 中段高亮 + 后段普通
            if start[1] > 0:
                result.append(("class:cli-default", text[:start[1]]))
            result.append(("class:cli-selection", text[start[1]:end[1]]))
            if end[1] < len(text):
                result.append(("class:cli-default", text[end[1]:]))
        elif frag_idx == start[0]:
            # 起点 fragment：前段普通 + 后段高亮
            if start[1] > 0:
                result.append(("class:cli-default", text[:start[1]]))
            result.append(("class:cli-selection", text[start[1]:]))
        elif frag_idx == end[0]:
            # 终点 fragment：前段高亮 + 后段普通
            result.append(("class:cli-selection", text[:end[1]]))
            if end[1] < len(text):
                result.append(("class:cli-default", text[end[1]:]))
        else:
            # 中间 fragment：完整高亮
            result.append(("class:cli-selection", text))

        return result

    def _flatten_transcript_for_pt() -> list[Any]:
        """Expand stored ``ANSI(...)`` rows to plain (style, text) fragments.

        ``to_formatted_text`` treats top-level lists as already normalized and does
        not recurse into items, so a mix of tuples and ``ANSI`` breaks ``split_lines``.

        边框线（border）按视口宽度截断，不随 ``wrap_lines`` 折行。

        **复制模式支持**：当复制模式激活且有选择范围时，对选中内容应用高亮样式。

        **安全过滤**：所有样式在输出前都经过 _is_valid_pt_style 验证，
        防止 emoji 等无效字符被 prompt_toolkit 解析为颜色值。
        """
        _recheck_md_width()
        from prompt_toolkit.formatted_text.ansi import ANSI as PTANSI
        from prompt_toolkit.formatted_text.base import to_formatted_text

        vp = _viewport_cols()
        out: list[Any] = []

        for frag_idx, frag in enumerate(_transcript):
            # 复制模式下应用选择高亮
            if _copy_mode_active[0] and _selection_start[0] is not None:
                if isinstance(frag, tuple) and len(frag) >= 2:
                    style_cls, text = frag[0], frag[1]
                    # 安全验证样式
                    if not _is_valid_pt_style(style_cls):
                        style_cls = ""
                    if style_cls in _BORDER_CLASSES:
                        text = _border_truncate(text, vp)
                    # 应用选择高亮
                    highlighted = _apply_selection_highlight(frag_idx, text)
                    # 保留原始样式前缀（如果不是选择区域）
                    for h_style, h_text in highlighted:
                        if h_style == "class:cli-selection":
                            out.append((h_style, h_text))
                        else:
                            out.append((style_cls, h_text))
                elif isinstance(frag, PTANSI):
                    # ANSI 对象：获取纯文本并应用高亮
                    from miniagent.engine.markdown_cli import strip_ansi
                    plain_text = strip_ansi(frag.value)
                    highlighted = _apply_selection_highlight(frag_idx, plain_text)
                    for h_style, h_text in highlighted:
                        out.append((h_style, h_text))
                else:
                    ansi_list = to_formatted_text(frag)
                    # 安全过滤 + 截断
                    safe_list = _truncate_hrule_in_ansi(ansi_list, vp)
                    # 对 ANSI 列表也应用选择处理
                    plain_text = "".join(item[1] if isinstance(item, tuple) and len(item) >= 2 else "" for item in safe_list)
                    highlighted = _apply_selection_highlight(frag_idx, plain_text)
                    for h_style, h_text in highlighted:
                        out.append((h_style, h_text))
            else:
                # 正常模式：原有渲染逻辑
                if isinstance(frag, tuple) and len(frag) >= 2:
                    style_cls, text = frag[0], frag[1]
                    # 安全验证样式
                    if not _is_valid_pt_style(style_cls):
                        style_cls = ""
                    if style_cls in _BORDER_CLASSES:
                        text = _border_truncate(text, vp)
                    out.append((style_cls, text))
                elif isinstance(frag, PTANSI):
                    ansi_list = to_formatted_text(frag)
                    out.extend(_truncate_hrule_in_ansi(ansi_list, vp))
                else:
                    ansi_list = to_formatted_text(frag)
                    out.extend(_truncate_hrule_in_ansi(ansi_list, vp))
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

    def _markdown_render_width() -> int:
        """Markdown 渲染宽度：基于视口宽度，足够宽以保证可读性。

        仅扣除最小边距（1列），滚动条已在 _viewport_cols 中扣除。
        """
        from miniagent.core.constants import CLI_WIDTH_MARGIN

        return _markdown_render_width_for_vp(_viewport_cols(), CLI_WIDTH_MARGIN)

    # ─── 水平滚动控制 ───────────────────────────────────────────────
    from miniagent.core.constants import CLI_WRAP_THRESHOLD

    _WRAP_LINES_THRESHOLD = CLI_WRAP_THRESHOLD
    _horizontal_scroll = [0]  # 水平滚动偏移（可变）
    _drag_start_x = [None]  # 水平拖动起始 X 坐标
    _dragging_scrollbar = [False]  # 正在拖动垂直滚动条
    _drag_start_y = [0]  # 滚动条拖动起始 Y 坐标
    _SCROLLBAR_WIDTH = 2  # 滚动条宽度（右侧约 1-2 列）
    _transcript_window_ref = [None]  # Window 引用（用于设置 horizontal_scroll）

    def _should_wrap_lines() -> bool:
        """检测是否应该折行：宽度足够时折行，太窄时启用水平滚动。"""
        return _viewport_cols() >= _WRAP_LINES_THRESHOLD

    def _max_horizontal_scroll() -> int:
        """水平滚动最大值：估计内容宽度 - 视口宽度。

        简化实现：使用 2 倍视口宽度作为内容宽度估计，
        确保足够大的滚动范围。
        """
        vp = _viewport_cols()
        return max(0, vp * 2)  # 允许滚动到 2 倍视口宽度

    def _apply_horizontal_scroll(delta: int) -> None:
        """执行水平滚动。"""
        new_val = max(0, min(_max_horizontal_scroll(), _horizontal_scroll[0] + delta))
        _horizontal_scroll[0] = new_val
        w = _transcript_window_ref[0]
        if w is not None:
            w.horizontal_scroll = new_val

    def _reset_horizontal_scroll() -> None:
        """重置水平滚动（切换回折行模式时调用）。"""
        _horizontal_scroll[0] = 0
        w = _transcript_window_ref[0]
        if w is not None:
            w.horizontal_scroll = 0

    def _is_scrollbar_click(mouse_event: MouseEvent) -> bool:
        """检测是否点击在滚动条区域（右侧约 1-2 列）。"""
        try:
            vp_cols = _viewport_cols()
            # MouseEvent.position 是 Point(x, y)
            click_x = getattr(mouse_event.position, "x", 0)
            return click_x >= vp_cols - _SCROLLBAR_WIDTH
        except Exception:
            return False

    def _content_preferred_height() -> int:
        """transcript 内容理想高度（用于计算最大滚动偏移）。

        注意：依赖 ScrollablePane 的内置高度计算，不添加额外估算逻辑。
        估算逻辑可能导致滚动范围被错误限制。
        """
        try:
            sp = _sp()
            if sp is None:
                return 0
            ph = sp.content.preferred_height(_viewport_cols(), sp.max_available_height)
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
        """signed_step<0: older; >0: newer. Drives ScrollablePane.vertical_scroll.

        增强版本：添加调试日志，便于验证滚动功能工作正常。
        """
        sp = _sp()
        if sp is None:
            _logger.debug(f"滚动失败: ScrollablePane 引用为 None (source={src})")
            return

        # 调试日志：记录滚动请求
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(
                f"滚动请求: source={src}, step={signed_step}, "
                f"current={sp.vertical_scroll}, max={_max_output_scroll()}"
            )

        _stick_bottom[0] = False
        step = max(1, abs(signed_step))
        before = sp.vertical_scroll
        mx = _max_output_scroll()
        if signed_step < 0:
            sp.vertical_scroll = max(0, before - step)
        else:
            sp.vertical_scroll = min(mx, before + step)

        # 调试日志：记录滚动结果
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(
                f"滚动结果: new_position={sp.vertical_scroll}, "
                f"viewport_rows={_viewport_rows()}, content_height={_content_preferred_height()}"
            )

        # 检测是否接近顶部，触发加载更多历史
        if sp.vertical_scroll < 5 and signed_step < 0:
            _trigger_lazy_load_more_history()

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
            """委托内层高度计算。"""
            return self._inner.preferred_height(
                width, max_available_height, wrap_lines, get_line_prefix
            )

        def create_content(self, width: int, height: int) -> UIContent:
            """委托内层生成 ``UIContent``。"""
            return self._inner.create_content(width, height)

        def mouse_handler(self, mouse_event: MouseEvent) -> NotImplemented | None:
            """滚轮事件改为驱动 ScrollablePane 纵向滚动；
            滚动条区域支持点击/拖动；非折行模式支持水平拖动。

            **复制模式支持**：复制模式下，鼠标事件用于选择文本。
            """
            # ─── 复制模式处理 ───────────────────────────────────────────
            if _copy_mode_active[0]:
                return self._handle_copy_mode_mouse(mouse_event)

            # ─── 垂直滚轮 ───────────────────────────────────────────
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                _apply_transcript_scroll(-_wheel_line_step(), "mouse.SCROLL_UP")
                get_app().invalidate()
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                _apply_transcript_scroll(_wheel_line_step(), "mouse.SCROLL_DOWN")
                get_app().invalidate()
                return None

            sp = _sp()

            # ─── 滚动条拖动（持续处理） ───────────────────────────────────
            # 优先检查拖动状态，而不是点击位置（用户可能拖出滚动条区域）
            if _dragging_scrollbar[0]:
                if sp is None:
                    _dragging_scrollbar[0] = False
                    return self._inner.mouse_handler(mouse_event)

                if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
                    try:
                        current_y = getattr(mouse_event.position, "y", 0)
                        delta_y = current_y - _drag_start_y[0]
                        vp_rows = _viewport_rows()
                        max_scroll = _max_output_scroll()
                        scroll_delta = int(delta_y * max_scroll / vp_rows) if vp_rows > 0 else 0
                        sp.vertical_scroll = max(0, min(max_scroll, sp.vertical_scroll + scroll_delta))
                        _drag_start_y[0] = current_y
                        get_app().invalidate()
                    except Exception:
                        pass
                    return None
                elif mouse_event.event_type == MouseEventType.MOUSE_UP:
                    _dragging_scrollbar[0] = False
                    return None

            # ─── 水平拖动（持续处理） ───────────────────────────────────
            # 优先检查水平拖动状态（用户可能拖出原始区域）
            if _drag_start_x[0] is not None and not _should_wrap_lines():
                if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
                    try:
                        current_x = getattr(mouse_event.position, "x", 0)
                        delta = _drag_start_x[0] - current_x
                        _apply_horizontal_scroll(delta)
                        _drag_start_x[0] = current_x
                        get_app().invalidate()
                    except Exception:
                        pass
                    return None
                elif mouse_event.event_type == MouseEventType.MOUSE_UP:
                    _drag_start_x[0] = None
                    return None

            # ─── 新点击/拖动开始 ───────────────────────────────────────
            # 滚动条点击开始拖动
            if _is_scrollbar_click(mouse_event) and mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                if sp is not None:
                    _dragging_scrollbar[0] = True
                    try:
                        _drag_start_y[0] = mouse_event.position.y
                    except Exception:
                        _drag_start_y[0] = 0
                    # 点击时直接跳到对应位置
                    try:
                        vp_rows = _viewport_rows()
                        max_scroll = _max_output_scroll()
                        click_y = getattr(mouse_event.position, "y", 0)
                        fraction = click_y / vp_rows if vp_rows > 0 else 0
                        new_scroll = int(fraction * max_scroll)
                        sp.vertical_scroll = max(0, min(max_scroll, new_scroll))
                        get_app().invalidate()
                    except Exception:
                        pass
                return None

            # 水平拖动开始（非折行模式）
            if not _should_wrap_lines() and mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                try:
                    _drag_start_x[0] = mouse_event.position.x
                except Exception:
                    _drag_start_x[0] = 0
                return None

            return self._inner.mouse_handler(mouse_event)

        def _handle_copy_mode_mouse(self, mouse_event: MouseEvent) -> NotImplemented | None:
            """复制模式下处理鼠标选择。

            简化实现：基于屏幕坐标估算 transcript 位置。
            完整实现需要精确的坐标到文本映射（复杂度较高）。
            """
            # 空 transcript 时不允许选择
            if len(_transcript) == 0:
                return NotImplemented

            try:
                click_y = getattr(mouse_event.position, "y", 0)
                click_x = getattr(mouse_event.position, "x", 0)
            except Exception:
                return NotImplemented

            sp = _sp()
            if sp is None:
                return NotImplemented

            # 计算屏幕行到 transcript 内容的映射
            scroll_offset = sp.vertical_scroll
            vp_rows = _viewport_rows()

            # 估算全局字符位置（基于屏幕坐标比例）
            if vp_rows > 0:
                abs_line = scroll_offset + click_y
                vp_cols = _viewport_cols()
                approx_char_pos = abs_line * vp_cols + click_x
            else:
                approx_char_pos = click_x

            # 将字符位置映射到 (fragment_idx, char_offset)
            # 初始化为最后一个 fragment 的末尾（超出范围时的默认值）
            target_frag_idx = len(_transcript) - 1
            target_char_offset = _get_transcript_char_count(target_frag_idx)
            char_accum = 0

            for frag_idx in range(len(_transcript)):
                frag_len = _get_transcript_char_count(frag_idx)
                if char_accum + frag_len > approx_char_pos:
                    target_frag_idx = frag_idx
                    target_char_offset = max(0, min(frag_len, approx_char_pos - char_accum))
                    break
                char_accum += frag_len

            # 处理鼠标事件
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                # 开始选择：记录起点
                _selection_start[0] = (target_frag_idx, target_char_offset)
                _selection_end[0] = (target_frag_idx, target_char_offset)
                _copy_mode_mouse_down[0] = True
                get_app().invalidate()
                return None

            elif mouse_event.event_type == MouseEventType.MOUSE_MOVE:
                # 拖动选择：更新终点
                if _copy_mode_mouse_down[0]:
                    _selection_end[0] = (target_frag_idx, target_char_offset)
                    _selection_text[0] = _extract_selection_text()
                    get_app().invalidate()
                return None

            elif mouse_event.event_type == MouseEventType.MOUSE_UP:
                # 结束选择
                _copy_mode_mouse_down[0] = False
                # 确保终点有效
                if _selection_start[0] is not None:
                    _selection_end[0] = (target_frag_idx, target_char_offset)
                    _selection_text[0] = _extract_selection_text()
                get_app().invalidate()
                return None

            return NotImplemented

    transcript_inner = FormattedTextControl(
        text=_flatten_transcript_for_pt,
        focusable=False,
    )
    transcript_window = Window(
        _TranscriptPaneControl(transcript_inner),
        wrap_lines=Condition(_should_wrap_lines),  # 动态控制：宽度足够时折行，太窄时启用水平滚动
    )
    _transcript_window_ref[0] = transcript_window  # 保存引用用于水平滚动控制
    output_scroll = ScrollablePane(
        transcript_window,
        height=D(weight=1),
        keep_cursor_visible=False,
        keep_focused_window_visible=False,
        show_scrollbar=True,
    )
    _output_scroll_ref[0] = output_scroll

    # ─── 水平滚动条 UI ───────────────────────────────────────────────
    def _render_horizontal_scrollbar() -> list[tuple[str, str]]:
        """渲染水平滚动条为 FormattedText。

        格式：◀ ░░░░█░░░░ ▶（左箭头 + 轨道 + 滑块 + 右箭头）
        仅在 _should_wrap_lines() == False 时显示。
        """
        if _should_wrap_lines():
            return [("class:cli-spacer", "")]  # 折行模式时隐藏

        vp = _viewport_cols()
        max_scroll = _max_horizontal_scroll()
        current_scroll = _horizontal_scroll[0]

        if max_scroll <= 0:
            return [("class:cli-spacer", "")]  # 无滚动内容时隐藏

        # 计算滑块位置和宽度
        # 内容总宽度 = vp + max_scroll（视口 + 可滚动范围）
        content_width = vp + max_scroll
        fraction_visible = vp / float(content_width) if content_width > 0 else 1.0
        fraction_scrolled = current_scroll / float(content_width) if content_width > 0 else 0.0

        # 滑块宽度（至少 2 字符）
        thumb_width = max(2, int(vp * fraction_visible))
        # 滑块位置（相对于视口）
        thumb_pos = min(vp - thumb_width, int(vp * fraction_scrolled))

        # 构建滚动条字符
        # 左箭头区域：2 字符
        # 轨道区域：vp - 4 字符
        # 右箭头区域：2 字符
        track_width = vp - 4

        result: list[tuple[str, str]] = []

        # 左箭头
        if current_scroll > 0:
            result.append(("class:hsb-arrow", "◀ "))  # ◀ 实心箭头（可点击）
        else:
            result.append(("class:hsb-arrow-disabled", "◁ "))  # ◁ 空心箭头（禁用）

        # 轨道 + 滑块
        for i in range(track_width):
            if thumb_pos <= i < thumb_pos + thumb_width:
                # 滑块位置
                result.append(("class:hsb-thumb", "█"))  # █ 全实心块
            else:
                # 轨道背景
                result.append(("class:hsb-track", "░"))  # ░ 25% 实心块

        # 右箭头
        if current_scroll < max_scroll:
            result.append(("class:hsb-arrow", " ▶"))  # ▶ 实心箭头（可点击）
        else:
            result.append(("class:hsb-arrow-disabled", " ▷"))  # ▷ 空心箭头（禁用）

        return result

    class _HorizontalScrollbarControl(UIControl):
        """水平滚动条控件，支持鼠标交互。"""

        __slots__ = ()

        def preferred_width(self, max_available_width: int) -> int | None:
            # 占满可用宽度（返回 int，而非 Dimension）
            return max_available_width

        def preferred_height(self, width: int, max_available_height: int, wrap_lines: bool, get_line_prefix) -> int | None:
            # 仅在非折行模式且有水平滚动需求时显示（返回 int，而非 Dimension）
            if not _should_wrap_lines() and _max_horizontal_scroll() > 0:
                return 1
            return 0

        def create_content(self, width: int, height: int) -> UIContent:
            # UIContent 需要 get_line 回调，而非 formatted_text
            ft = _render_horizontal_scrollbar()
            return UIContent(
                get_line=lambda i: ft if i == 0 else [],
                line_count=1,
                show_cursor=False,
            )

        def mouse_handler(self, mouse_event: MouseEvent) -> NotImplemented | None:
            """处理水平滚动条鼠标事件。"""
            if _should_wrap_lines():
                return NotImplemented

            vp = _viewport_cols()
            max_scroll = _max_horizontal_scroll()

            if max_scroll <= 0:
                return NotImplemented

            click_x = getattr(mouse_event.position, "x", 0)

            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                # 点击左箭头（x < 2）
                if click_x < 2:
                    _apply_horizontal_scroll(-20)
                    get_app().invalidate()
                    return None
                # 点击右箭头（x >= vp - 2）
                elif click_x >= vp - 2:
                    _apply_horizontal_scroll(20)
                    get_app().invalidate()
                    return None
                # 点击轨道/滑块（2 <= x < vp - 2）
                else:
                    track_width = vp - 4
                    track_x = click_x - 2
                    if track_width > 0:
                        fraction = track_x / float(track_width)
                        new_scroll = int(fraction * max_scroll)
                        _horizontal_scroll[0] = max(0, min(max_scroll, new_scroll))
                        w = _transcript_window_ref[0]
                        if w is not None:
                            w.horizontal_scroll = _horizontal_scroll[0]
                        get_app().invalidate()
                    return None

            elif mouse_event.event_type == MouseEventType.MOUSE_MOVE:
                # 鼠标拖动（可选实现，暂时跳过）
                return None

            return NotImplemented

    h_scrollbar_window = Window(
        _HorizontalScrollbarControl(),
        dont_extend_width=True,
        dont_extend_height=True,  # 让控件动态决定高度，避免强制占用空间
    )

    def _append_transcript(style_cls: str, text: str = "", *, ansi: Any = None) -> None:
        """向 transcript 追加样式化文本；同样式尾部合并；维护粘底与长度裁剪。

        性能优化：维护累计长度计数器，避免每次遍历计算。

        **安全验证**：样式在存储前经过 _is_valid_pt_style 验证，
        无效样式替换为空字符串，防止后续渲染错误。
        """
        if not text and ansi is None:
            return
        # 安全验证样式
        if not _is_valid_pt_style(style_cls):
            style_cls = ""
        at_bottom = _output_at_bottom()
        if (
            _transcript
            and isinstance(_transcript[-1], tuple)
            and len(_transcript[-1]) >= 2
            and _transcript[-1][0] == style_cls
        ):
            st, prev = _transcript[-1]
            # 性能优化：更新累计长度（差值而非遍历）
            new_text = prev + text
            _transcript_total_len[0] += len(text)  # 增加新增文本长度
            _transcript[-1] = (st, new_text)
        else:
            if ansi is not None:
                _transcript.append(ansi)
                _transcript_total_len[0] += _transcript_fragment_len(ansi)
            else:
                _transcript.append((style_cls, text))
                _transcript_total_len[0] += len(text)  # 性能优化：直接累加
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
        from miniagent.engine.cli_transcript import transcript_plain

        return transcript_plain(list(_transcript))

    def _append_ansi_transcript(ansi_obj: Any) -> None:
        """向 transcript 直接追加 ANSI 对象，含 trim/scroll 管理。

        性能优化：更新累计长度计数器。
        """
        at_bottom = _output_at_bottom()
        _transcript.append(ansi_obj)
        # 性能优化：更新累计长度
        _transcript_total_len[0] += _transcript_fragment_len(ansi_obj)
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

    kb = KeyBindings()

    # ─── Tab 自动补全（用户体验增强）──────────────────────────────────────
    @kb.add("tab", filter=has_focus(input_buffer))
    def _on_tab(event):
        """Tab 键触发自动补全（命令或文件路径）。"""
        # prompt_toolkit 的 BufferControl 会自动处理补全
        # 当 completer 设置后，start_completion() 会显示补全菜单
        event.app.current_buffer.start_completion()

    @kb.add("s-tab", filter=has_focus(input_buffer))  # Shift+Tab
    def _on_shift_tab(event):
        """Shift+Tab 向前循环补全选项。"""
        event.app.current_buffer.complete_previous()

    # ─── 复制模式键绑定（eager=True 确保优先级高于正常模式）───────────────────
    @kb.add("c-m")
    def _on_ctrl_m(event):
        """Ctrl+M 切换复制模式。"""
        _toggle_copy_mode()
        if _copy_mode_active[0]:
            # 进入复制模式：显示提示
            _append_transcript("class:cli-copy-mode-hint", "\n[复制模式] 拖动鼠标选择 · Ctrl+C复制 · Enter复制并退出 · Esc取消 · a全选 · Ctrl+M退出\n")
            _stick_bottom[0] = True
        else:
            # 退出复制模式：清除提示和选择
            _clear_selection()

    # 复制模式专用过滤器
    def _in_copy_mode() -> bool:
        """prompt_toolkit 过滤器：是否处于 transcript 复制模式。"""
        return _copy_mode_active[0]

    # 使用 eager=True 确保复制模式下这些键优先处理，不传递给正常模式
    @kb.add("c-c", eager=True, filter=Condition(_in_copy_mode))
    def _on_copy_mode_ctrl_c(event):
        """复制模式下 Ctrl+C 复制选中内容。"""
        text = _selection_text[0]
        if text:
            if copy_text_to_system_clipboard(text):
                _append_transcript("class:cli-ok", f"\n{SUCCESS_PREFIX} 已复制 {len(text)} 字符\n")
            else:
                _append_transcript("class:cli-err", f"\n{ERROR_PREFIX} 复制失败（剪贴板不可用）\n")
        else:
            _append_transcript("class:cli-warn", f"\n{WARNING_PREFIX} 请先选择内容\n")
        _stick_bottom[0] = True

    @kb.add("enter", eager=True, filter=Condition(_in_copy_mode))
    def _on_copy_mode_enter(event):
        """复制模式下 Enter 复制并退出。"""
        text = _selection_text[0]
        if text:
            if copy_text_to_system_clipboard(text):
                _append_transcript("class:cli-ok", f"\n{SUCCESS_PREFIX} 已复制 {len(text)} 字符并退出复制模式\n")
            else:
                _append_transcript("class:cli-err", f"\n{ERROR_PREFIX} 复制失败\n")
        _toggle_copy_mode()
        _stick_bottom[0] = True

    @kb.add("escape", eager=True, filter=Condition(_in_copy_mode))
    def _on_copy_mode_escape(event):
        """复制模式下 Escape 取消选择或退出复制模式。"""
        if _selection_start[0] is not None:
            # 有选择：取消选择
            _clear_selection()
        else:
            # 无选择：退出复制模式
            _toggle_copy_mode()

    @kb.add("a", eager=True, filter=Condition(_in_copy_mode))
    def _on_copy_mode_select_all(event):
        """复制模式下 a 全选。"""
        if len(_transcript) > 0:
            # 起点：第一个 fragment 的开头
            _selection_start[0] = (0, 0)
            # 终点：最后一个 fragment 的末尾
            last_idx = len(_transcript) - 1
            last_len = _get_transcript_char_count(last_idx)
            if last_len > 0:
                _selection_end[0] = (last_idx, last_len)
                _selection_text[0] = _extract_selection_text()
                _append_transcript("class:cli-ok", f"\n{SUCCESS_PREFIX} 已全选 {len(_selection_text[0])} 字符\n")
            else:
                _append_transcript("class:cli-warn", f"\n{WARNING_PREFIX} 内容为空\n")
            _stick_bottom[0] = True

    # ─── 正常模式键绑定 ───────────────────────────────────────────
    @kb.add("enter", filter=has_focus(input_buffer))
    def _on_enter(event):
        """回车提交输入"""
        text = input_buffer.text.strip()
        if text:
            # 检测特殊前缀
            if text.startswith("!"):
                bash_cmd = text[1:].strip()
                if bash_cmd:
                    ok, output = run_cli_bash_command(bash_cmd)
                    style = "class:cli-default" if ok else "class:cli-err"
                    _append_transcript(style, output)
                    _stick_bottom[0] = True
                    event.app.invalidate()
                input_buffer.reset(append_to_history=True)
                return
            input_buffer.reset(append_to_history=True)
            event.app.exit(result=text)

    @kb.add("c-c", filter=has_focus(input_buffer))
    def _on_ctrl_c(event):
        """Ctrl+C 退出"""
        event.app.exit(result="__exit__")

    @kb.add("c-d", filter=has_focus(input_buffer))
    def _on_ctrl_d(event):
        """Ctrl+D 退出程序"""
        event.app.exit(result="__exit__")

    @kb.add("c-l", filter=has_focus(input_buffer))
    def _on_ctrl_l(event):
        """Ctrl+L 清屏重绘

        完整清屏流程：
        1. 清空 transcript 列表
        2. 重置所有状态计数器
        3. 重置滚动位置
        4. 重置历史加载状态
        5. 重新加载初始历史（保持用户体验）
        """
        _reset_and_reload_transcript(reset_scroll_to_top=True)
        event.app.invalidate()

    @kb.add("c-t", filter=has_focus(input_buffer))
    def _on_ctrl_t(event):
        """Ctrl+T 显示后台任务列表"""
        from miniagent.engine.btw_cmd import cmd_btw_status
        status_text = cmd_btw_status()
        # 使用term_write显示到transcript
        term_write(status_text + "\n", "ansicyan")
        _stick_bottom[0] = True
        event.app.invalidate()

    def _scroll_step() -> int:
        """PageUp/PageDown 一次滚动的行数（约为半屏）。"""
        return max(1, _viewport_rows() // 2)

    @kb.add("pageup", filter=has_focus(input_buffer))
    def _on_pageup(event):
        """上翻输出区约半屏。"""
        _apply_transcript_scroll(-_scroll_step(), "pageup")
        event.app.invalidate()

    @kb.add("pagedown", filter=has_focus(input_buffer))
    def _on_pagedown(event):
        """下翻输出区约半屏。"""
        _apply_transcript_scroll(_scroll_step(), "pagedown")
        event.app.invalidate()

    # ─── 水平滚动键盘绑定 ───────────────────────────────────────────
    @kb.add("s-left", filter=has_focus(input_buffer))
    def _on_shift_left(event):
        """Shift+Left: 水平向左滚动（仅非折行模式）。"""
        if not _should_wrap_lines():
            _apply_horizontal_scroll(-10)
            event.app.invalidate()

    @kb.add("s-right", filter=has_focus(input_buffer))
    def _on_shift_right(event):
        """Shift+Right: 水平向右滚动（仅非折行模式）。"""
        if not _should_wrap_lines():
            _apply_horizontal_scroll(10)
            event.app.invalidate()

    @kb.add("c-home", filter=has_focus(input_buffer))
    def _on_ctrl_home(event):
        """Ctrl+Home: 光标跳到输入开头。"""
        input_buffer.cursor_position = 0
        event.app.invalidate()

    @kb.add("c-end", filter=has_focus(input_buffer))
    def _on_ctrl_end(event):
        """Ctrl+End: 光标跳到输入末尾。"""
        input_buffer.cursor_position = len(input_buffer.text)
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

    def _ensure_input_history_ready_for_nav() -> None:
        """在 ↑↓ 导航前确保 working_lines 已同步填充。"""
        hist = getattr(input_buffer, "history", None)
        if hist is None:
            return
        get_strings = getattr(hist, "get_strings", None)
        if get_strings is None:
            return
        if len(input_buffer._working_lines) <= 1 and get_strings():
            _sync_preload_buffer_working_lines(input_buffer)

    # 显式绑定上下方向键到输入历史导航（不依赖 BufferControl 默认行为）
    @kb.add("up", filter=has_focus(input_buffer))
    def _on_up(event):
        """上方向键：浏览持久化输入历史（``history.txt`` + 启动时并入的会话 user 消息）。"""
        if input_buffer.complete_state:
            input_buffer.complete_previous()
        else:
            input_buffer.load_history_if_not_yet_loaded()
            _ensure_input_history_ready_for_nav()
            input_buffer.history_backward()
        event.app.invalidate()

    @kb.add("down", filter=has_focus(input_buffer))
    def _on_down(event):
        """下方向键：浏览持久化输入历史（``history.txt`` + 启动时并入的会话 user 消息）。"""
        if input_buffer.complete_state:
            input_buffer.complete_next()
        else:
            input_buffer.load_history_if_not_yet_loaded()
            _ensure_input_history_ready_for_nav()
            input_buffer.history_forward()
        event.app.invalidate()

    # PT 的 _parse_style_str 只认属性词 "dim"，不认 "ansidim"（后者会走 parse_color → ValueError）。
    from miniagent.core.constants import CLI_STYLE_THINK_BODY, CLI_STYLE_THINK_HEAD

    # ── CLI 样式配置（思考颜色可配置）──
    _cli_style_dict = {
        "prompt-prefix": "bold ansigreen",
        "cli-border-strong": "ansibrightblue bold",
        "cli-border": "ansiblue dim",
        "cli-user-title": "bold ansicyan",
        "cli-user-body": "ansicyan",
        # 思考样式：从配置读取，默认亮青色（淡雅清新，不扎眼）
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
        "cli-spacer": "",
        # ─── 复制模式样式 ───────────────────────────────────────────
        "cli-selection": "bg:ansicyan fg:ansiblack bold",  # 选择高亮：青色背景 + 黑色文字
        "cli-copy-mode-hint": "ansiyellow bold",  # 复制模式提示：黄色加粗
        # 滚动条样式增强（高对比度，确保在不同终端配色下可见）
        "scrollbar.button": "bg:ansibrightcyan fg:ansiblack",  # 滑块：亮青色背景 + 黑色文字（高对比）
        "scrollbar.background": "bg:ansibrightblack",          # 轨道：亮黑色背景
        "scrollbar.arrow": "ansiwhite bold",             # 箭头：白色加粗（更醒目）
        # 水平滚动条样式
        "hsb-thumb": "bg:ansibrightcyan fg:ansiblack",  # 滑块：亮青色背景 + 黑色文字
        "hsb-track": "bg:ansibrightblack",              # 轨道：亮黑色背景
        "hsb-arrow": "ansiwhite bold",            # 箭头：白色加粗
        "hsb-arrow-disabled": "ansibrightblack dim",    # 禁用箭头：灰色暗淡
        # ─── 补全菜单样式 ───────────────────────────────────────────
        "completion-menu": "bg:ansibrightblack fg:ansiwhite",
        "completion-menu.completion": "bg:ansibrightblack fg:ansiwhite",
        "completion-menu.completion.current": "bg:ansicyan fg:ansiblack bold",
        "completion-menu.meta": "bg:ansibrightblack fg:ansibrightblack dim",
        "completion-menu.meta.current": "bg:ansicyan fg:ansiblack dim",
    }
    cli_style = Style.from_dict(_cli_style_dict)

    body = FloatContainer(
        HSplit(
            [
                output_scroll,
                h_scrollbar_window,  # 水平滚动条（仅在窄窗口时显示）
                Window(
                    FormattedTextControl(
                        HTML(
                            "<cli-hint>PgUp/PgDn · 滚轮 · Shift+←/→ 水平滚动 · "
                            "Ctrl+Home/End 移光标 · "
                            "Ctrl+M 复制模式 · "
                            "/copy 复制全部对话 · "
                            "新消息时自动跟随输出</cli-hint>"
                        )
                    ),
                    height=D.exact(1),
                ),
                Window(height=1, char="─", style="class:cli-border"),
                VSplit(
                    [
                        Window(
                            FormattedTextControl(HTML("<prompt-prefix>❯ </prompt-prefix><cli-muted>↑↓历史</cli-muted>")),
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
        ),
        floats=[
            Float(
                xcursor=True,  # 水平位置跟随光标
                ycursor=True,  # 垂直位置在光标下方（补全菜单通常显示在光标下方）
                content=CompletionsMenu(max_height=10),
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
    _last_md_width[0] = _viewport_cols()  # 记录初始终端宽度
    ctx.cli_transcript_append = _append_transcript
    ctx.cli_transcript_append_ansi = _append_ansi_transcript
    from miniagent.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator

    def _clear_stream_state(sk: str) -> None:
        """turn 结束时清除指定 session 的流式思考累加器。"""
        _streaming_think_by_session.pop(sk, None)

    _transcript_coordinator = CliTranscriptCoordinator(
        _append_transcript,
        _append_ansi_transcript,
        on_turn_end=_clear_stream_state,
    )
    ctx.cli_transcript_coordinator = _transcript_coordinator
    ctx.create_feishu_handler_factory = lambda st: create_feishu_handler(st, ctx, _stick_bottom)
    # stderr 日志仍会打乱 VS Code 等与 PT 共用的终端画布；TUI 期间默认只打 WARNING+
    if not get_config("features.tui_verbose_log", False):
        set_console_log_threshold(logging.WARNING)

    _ANSI_COLOR_STYLE_MAP: dict[str, str] = {
        "ansiblue": "class:cli-border",
        "ansigreen": "class:cli-ok",
        "ansired": "class:cli-err",
        "ansiyellow": "class:cli-warn",
        "ansicyan": "class:cli-user-title",
    }

    def term_write(text: str = "", color: str = "") -> None:
        """写入上方 transcript。优先尝试 markdown 渲染，失败降级为样式文本。"""
        from miniagent.engine.markdown_cli import render_markdown_to_ansi

        if text == "":
            return
        if not text.endswith("\n"):
            text = text + "\n"

        # 安全检查：确保 color 不包含 emoji 或无效字符（防止 prompt_toolkit 解析错误）
        if color and not color.startswith("class:") and not color.startswith("ansi"):
            # 只允许 ansi* 或 class:* 格式
            color = ""

        try:
            md_w = _markdown_render_width()  # 统一使用更宽的渲染宽度
            ansi_body = render_markdown_to_ansi(text, width=md_w, justify="left")
            if ansi_body is not None:
                safe_ft = _safe_ansi(ansi_body)
                # 直接使用过滤后的 fragments，不创建 ANSI 对象
                for style, txt in safe_ft:
                    _transcript.append((style, txt))
                    _transcript_total_len[0] += len(txt)
                _trim_transcript()
            else:
                style = _ANSI_COLOR_STYLE_MAP.get(color, "class:cli-default")
                _append_transcript(style, text)
        except Exception:
            style = _ANSI_COLOR_STYLE_MAP.get(color, "class:cli-default")
            _append_transcript(style, text)

    def _thinking_sink_inner(
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        """思考输出核心逻辑（按 session 隔离流式状态）。"""
        sk = (session_key or "").strip() or "default"
        stream = _stream_state(sk)

        if ansi_markdown is not None:
            body_lines = ansi_markdown.rstrip("\n").split("\n")
            transcript_body = "\n".join(ln if ln else "" for ln in body_lines) + "\n"
            at_bottom = _output_at_bottom()
            safe_ft = _safe_ansi(transcript_body)
            for style, txt in safe_ft:
                _transcript.append((style, txt))
                _transcript_total_len[0] += len(txt)
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

        if kind == "label":
            stream.active = False
            stream.text = ""
            stream.start_idx = -1
            _append_transcript(style, fragment)
        else:
            from miniagent.engine.markdown_cli import render_markdown_to_ansi

            try:
                md_w = _markdown_render_width()

                if stream.active and stream.start_idx >= 0:
                    full_text = stream.text + fragment
                    stream.text = full_text

                    ansi_body = render_markdown_to_ansi(full_text, width=md_w, justify="left")
                    safe_ft = _safe_ansi(ansi_body)

                    start_idx = stream.start_idx

                    old_len = 0
                    for i in range(start_idx, len(_transcript)):
                        frag = _transcript[i]
                        if isinstance(frag, tuple) and len(frag) >= 2:
                            old_len += len(frag[1])
                    new_len = sum(len(txt) for _, txt in safe_ft)
                    _transcript_total_len[0] += new_len - old_len

                    while len(_transcript) > start_idx:
                        _transcript.pop()
                    _transcript.extend(safe_ft)

                else:
                    ansi_body = render_markdown_to_ansi(fragment, width=md_w, justify="left")
                    safe_ft = _safe_ansi(ansi_body)

                    start_idx = len(_transcript)
                    stream.start_idx = start_idx
                    stream.text = fragment
                    stream.active = True

                    for st, txt in safe_ft:
                        _transcript.append((st, txt))
                        _transcript_total_len[0] += len(txt)

                try:
                    get_app().invalidate()
                except Exception:
                    pass
                if _output_at_bottom() or _stick_bottom[0]:
                    _snap_output_bottom()
            except Exception:
                stream.active = False
                stream.text = ""
                stream.start_idx = -1
                _append_transcript(style, fragment)

    def _thinking_sink(
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        """``ThinkingDisplay`` 输出槽：经 coordinator 路由，缓冲轮次整轮 flush。"""
        sk = (session_key or "").strip() or "default"
        if _transcript_coordinator.is_live(sk):
            _thinking_sink_inner(fragment, kind, session_key=sk, ansi_markdown=ansi_markdown)
        else:
            _transcript_coordinator.defer(
                sk,
                lambda: _thinking_sink_inner(
                    fragment, kind, session_key=sk, ansi_markdown=ansi_markdown
                ),
            )

    engine.thinking.set_output_sink(_thinking_sink)
    engine.thinking.set_cli_markdown_width(_markdown_render_width)  # 统一使用 _markdown_render_width

    def _rule_line_width() -> int:
        """与 Markdown 渲染宽度同源，避免分隔线与正文视觉错位。"""
        return _rule_line_width_for_vp(_viewport_cols())

    state["cli_render_width"] = _rule_line_width
    state["cli_markdown_width"] = _markdown_render_width

    def _clear_cli_format_widths() -> None:
        state.pop("cli_render_width", None)
        state.pop("cli_markdown_width", None)

    def _cli_rule_heavy() -> None:
        """在 transcript 中画粗分隔线（双线条字符）。"""
        w = _rule_line_width()
        _append_transcript("class:cli-border-strong", "\u2550" * w + "\n")

    def _cli_rule_light() -> None:
        """在 transcript 中画细分隔线。"""
        w = _rule_line_width()
        _append_transcript("class:cli-border", "─" * w + "\n")

    def _cli_block_user(prompt: str) -> None:
        """本轮提问区块（委托 ``cli_format``，与实时轮次样式一致）。"""
        from miniagent.engine.cli_format import format_cli_user_block

        format_cli_user_block(
            _append_transcript,
            prompt,
            _stick_bottom,
            render_width=_rule_line_width(),
        )

    def _cli_block_reply(text: str) -> None:
        """最终回复区块（委托 ``cli_format``，含安全 ANSI 与粘底滚动）。"""
        from miniagent.engine.cli_format import format_cli_reply_block

        format_cli_reply_block(
            _append_transcript,
            _append_ansi_transcript,
            text,
            render_width=_rule_line_width(),
            markdown_width=_markdown_render_width(),
        )

    async def _process_input(user_input: str) -> None:
        """处理用户输入并打印回复（含 ``@file:`` 标记与 Agent 调用）。"""
        from miniagent.engine.cli_format import format_cli_reply_block, format_cli_user_block
        from miniagent.engine.parallel_config import resolve_active_session_key

        session_key = resolve_active_session_key(
            channel_router, state.get("active_session_id") or "default"
        )
        try:
            user_input, _files_info = await detect_and_process_file_markers(
                user_input,
                session_key,
                state.get("session_manager"),
                ctx,
                notify=term_write,
            )

            _transcript_coordinator.begin_turn(session_key, source="cli")
            cli_append = _transcript_coordinator.make_session_append(session_key)
            cli_append_ansi = _transcript_coordinator.make_session_append_ansi(session_key)
            # 整轮 turn（You 块 + 执行 + 答案块）纳入会话级串行边界：
            # 同一 session_key 的 CLI 与飞书 turn 严格排队、原子呈现，不交错；
            # 不同 session_key 仍可并行。锁内 run_agent 须传 _hold_session_lock=True。
            async with engine.session_turn(session_key):
                try:
                    _cli_rule_heavy()
                    _was_at_bottom = _output_at_bottom()
                    _stick_bottom[0] = True
                    try:
                        _snap_output_bottom()
                        get_app().invalidate()
                    except Exception:
                        pass
                    format_cli_user_block(
                        cli_append,
                        user_input,
                        _stick_bottom,
                        render_width=_rule_line_width(),
                    )
                    try:
                        await asyncio.sleep(0)
                        if _was_at_bottom:
                            _stick_bottom[0] = True
                            _snap_output_bottom()
                            get_app().invalidate()
                    except Exception:
                        pass
                    reply = await engine.run_agent_with_thinking(
                        user_input,
                        session_key,
                        _skill_tb(),
                        _skill_sp(),
                        registry=registry,
                        monitor=monitor,
                        session_manager=state.get("session_manager"),
                        channel_router=channel_router,
                        clawhub=ctx.clawhub,
                        memory_store=ctx.memory_store,
                        activity_log=ctx.activity_log,
                        keyword_index=ctx.keyword_index,
                        memory_context=ctx.memory_context,
                        client=ctx.openai_client,
                        cli_loop_state=state,
                        _hold_session_lock=True,
                    )
                    if reply and reply.strip():
                        format_cli_reply_block(
                            cli_append,
                            cli_append_ansi,
                            (reply or "").strip(),
                            render_width=_rule_line_width(),
                            markdown_width=_markdown_render_width(),
                        )
                finally:
                    _transcript_coordinator.end_turn(session_key)
        except Exception as e:
            _append_transcript("class:cli-err", f"{ERROR_PREFIX} 错误: {e}\n")

    # 加载初始历史到 transcript
    _reset_and_reload_transcript()

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
            _clear_cli_format_widths()
            await _run_cli_loop_fallback(
                ctx,
                state,
                skill_toolboxes,
                skill_prompts,
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

        # ── /copy（全屏区为 FormattedText，终端一般无法框选复制）──
        if user_input == "/copy":
            plain = _transcript_plain()
            if copy_text_to_system_clipboard(plain):
                term_write(
                    f"{SUCCESS_PREFIX} 已复制 {len(plain)} 字符到剪贴板\n",
                    "ansigreen",
                )
            else:
                term_write(
                    f"{ERROR_PREFIX} 复制失败（无剪贴板或缺少 "
                    "wl-copy / xclip / pbcopy / clip）\n",
                    "ansired",
                )
            continue

        # ── /stop ──
        if user_input == "/stop":
            await shutdown_runtime(
                ctx,
                state,
                reason="dot_stop_ptk",
                release_cli_session_lock=True,
                call_unregister=True,
            )
            term_write(f"{SUCCESS_PREFIX} 当前实例已停止", "ansigreen")
            break

        # ── 其余命令：统一走 dispatch（capture → transcript，避免 print 破坏全屏）──
        if user_input.startswith("/"):
            from miniagent.engine.command_dispatch import dispatch_command

            prev_session_id = state["active_session_id"]
            reply = await dispatch_command(
                user_input,
                state=state,
                engine=engine,
                registry=registry,
                monitor=monitor,
                skill_toolboxes=_skill_tb(),
                skill_prompts=get_skill_prompts_from_state(state) or skill_prompts,
                capture=True,
                allow_session_mutations_when_capture=True,
                feishu_user_status=_feishu_user_status_fn(ctx),
            )
            if state["active_session_id"] != prev_session_id:
                _reset_and_reload_transcript(reset_scroll_to_top=True)
                _reload_cli_input_history(state, input_buffer, history_file)
            if reply == "__EXIT__":
                break
            if reply is not None:
                term_write(reply + "\n")
                continue

        # ── 需求澄清追问拦截：普通消息自动注入为回答 ──
        from miniagent.engine.parallel_config import resolve_active_session_key

        active_sk = resolve_active_session_key(
            channel_router, state.get("active_session_id") or "default"
        )
        engine.set_active_session_key(active_sk)
        cc = engine.get_confirmation_channel(active_sk)
        if cc and cc.has_pending:
            from miniagent.types.confirmation import ConfirmationResult, ConfirmationStage

            if cc.pending.stage == ConfirmationStage.CLARIFICATION:
                cc.respond(ConfirmationResult.clarification_reply(user_input))
                continue

        # ── Agent 执行 ──
        await message_queue.dispatch_cli(_process_input(user_input))

        try:
            heartbeat()
        except Exception:
            pass

    # 清理
    _clear_cli_format_widths()
    set_console_log_threshold(logging.INFO)
    ctx.cli_transcript_append = None

    from miniagent.engine.session_continue import save_cli_session_state

    # 保存 CLI 上次会话状态（--continue 功能）
    save_cli_session_state(ctx, state)

    release_session_lock(state["active_session_id"])
    try:
        unregister_instance()
    except Exception:
        pass
    # 全屏 Application 已结束；直接打印告别
    print("\n\U0001f44b bye\n", file=sys.stdout, flush=True)


def _print_history_summary_fallback(
    session_manager: Any,
    session_id: str,
    *,
    rule_heavy: Any,
    rule_light: Any,
    get_width: Any,
    header: str | None = None,
) -> None:
    """简易 CLI：将最近会话历史打印到 stdout（启动或切换会话后）。"""
    if not session_manager or not session_id:
        return

    initial_count = int(get_config("memory.initial_history_count", 5))
    try:
        messages, total = session_manager.load_session_history_range(
            session_id,
            start_idx=0,
            count=initial_count,
        )
    except Exception as e:
        _logger.debug("fallback 历史加载失败: %s", e)
        return

    if not messages:
        return

    if header:
        print(header)

    from miniagent.engine.markdown_cli import cli_raw_markdown_enabled

    fb_w = get_width()
    for msg in messages:
        role = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            print()
            rule_heavy()
            print("You")
            rule_light()
            for line in content.splitlines():
                print(line)
            print()
        elif role == "assistant":
            print()
            rule_light()
            print("Assistant")
            rule_light()
            if cli_raw_markdown_enabled():
                for line in content.splitlines():
                    print(line)
            else:
                try:
                    from rich.console import Console
                    from rich.markdown import Markdown

                    Console(width=fb_w).print(Markdown(content))
                except ImportError:
                    for line in content.splitlines():
                        print(line)
            print()

    if total > len(messages):
        remaining = total - len(messages)
        print(f"\n[… 还有 {remaining} 条更早历史]\n")


async def _run_cli_loop_fallback(
    ctx: RuntimeContext,
    state: CliLoopState,
    skill_toolboxes: list,
    skill_prompts: list,
) -> None:
    """简易 CLI 循环（prompt_toolkit 不可用时回退）。

    **Linux 兼容性**：在 fallback 模式下也显示思考过程，
    通过 ThinkingDisplay.set_output_sink 设置回调，
    确保终端不支持全屏时也能看到 Agent 的思考内容。

    点命令与全屏 TUI 一致经 ``dispatch_command`` 分发（stdout 输出）；
    ``/copy`` 与 ``/stop`` 因输出语义不同仍在此单独处理。
    """
    engine = ctx.engine
    registry = ctx.registry
    monitor = ctx.monitor
    channel_router = ctx.channel_router
    message_queue = ctx.message_queue

    from miniagent.engine.session_lock import release_session_lock
    from miniagent.skills.snapshots import (
        get_skill_prompts_from_state,
        get_skill_toolboxes_from_state,
        join_skill_prompts,
    )

    def _skill_tb() -> list:
        """从 state 获取当前技能工具箱列表（fallback 到传入参数）。"""
        return get_skill_toolboxes_from_state(state) or skill_toolboxes

    def _skill_sp() -> str | None:
        """从 state 获取当前技能提示词拼接字符串（fallback 到传入参数）。"""
        return join_skill_prompts(get_skill_prompts_from_state(state) or skill_prompts)

    def _fb_get_width() -> int:
        """获取 fallback CLI 渲染宽度（动态适应终端大小）。"""
        return get_render_width(fallback_width=80)

    def _fb_rule_heavy() -> None:
        """非全屏 CLI 下的粗分隔线（stdout）- 动态宽度。"""
        w = _fb_get_width()
        print("═" * w)

    def _fb_rule_light() -> None:
        """非全屏 CLI 下的细分隔线（stdout）- 动态宽度。"""
        w = _fb_get_width()
        print("─" * w)

    def _fb_show_session_history(header: str | None = None) -> None:
        """打印当前活跃会话的最近历史（启动或切换后）。"""
        _print_history_summary_fallback(
            state.get("session_manager"),
            state.get("active_session_id", ""),
            rule_heavy=_fb_rule_heavy,
            rule_light=_fb_rule_light,
            get_width=_fb_get_width,
            header=header,
        )

    # ── Linux 兼容性：fallback 模式下的思考显示 ──
    _fallback_print_lock = threading.Lock()

    def _fallback_print_locked(text: str, *, end: str = "\n") -> None:
        """线程安全地向 stdout 打印（fallback 模式无 TUI transcript）。"""
        with _fallback_print_lock:
            print(text, end=end)
            sys.stdout.flush()

    def _fallback_transcript_append(style_cls: str, text: str = "") -> None:
        """Fallback CLI 的 transcript 回调（用于飞书镜像到 CLI）。"""
        if text:
            _fallback_print_locked(text)

    from miniagent.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator

    _fb_coordinator = CliTranscriptCoordinator(
        _fallback_transcript_append,
        None,
        parallel_sessions=True,
    )
    ctx.cli_transcript_coordinator = _fb_coordinator
    ctx.cli_transcript_append = _fallback_transcript_append

    def _fallback_thinking_sink_inner(
        text: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
    ) -> None:
        """Fallback CLI 思考输出核心逻辑（print 到 stdout）。"""
        if text:
            _fallback_print_locked(text, end="" if kind == "chunk" else "\n")

    def _fallback_thinking_sink(
        text: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        **kwargs: Any,
    ) -> None:
        """Fallback CLI 的思考输出（经 coordinator 路由，print 锁防交错）。"""
        sk = (session_key or "").strip() or "default"
        if _fb_coordinator.is_live(sk):
            _fallback_thinking_sink_inner(text, kind, session_key=sk)
        else:
            _fb_coordinator.defer(
                sk,
                lambda: _fallback_thinking_sink_inner(text, kind, session_key=sk),
            )

    def _fb_file_notify(msg: str, _color: str = "") -> None:
        """Fallback 模式下 ``@file:`` 处理的用户提示（stdout，无颜色）。"""
        _fallback_print_locked(msg if msg.endswith("\n") else msg + "\n")

    engine.thinking.set_output_sink(_fallback_thinking_sink)


    # readline 支持：与全屏 TUI 共用 ``{state_dir}/cli/history.txt``
    history_file = _resolve_cli_history_file()
    try:
        import readline

        readline.set_history_length(1000)
        if os.path.isfile(history_file):
            readline.read_history_file(history_file)
        _prime_fallback_readline_history(history_file)
    except ImportError:
        readline = None  # Windows 可能无 readline

    _fb_show_session_history()

    async def _process_input(user_input: str) -> None:
        """备用终端：打印 You/Assistant 区块并调用 ``run_agent_with_thinking``。"""
        from miniagent.engine.cli_format import format_cli_reply_block, format_cli_user_block
        from miniagent.engine.parallel_config import resolve_active_session_key

        session_key = resolve_active_session_key(
            channel_router, state.get("active_session_id") or "default"
        )
        stick = [False]
        try:
            user_input, _files_info = await detect_and_process_file_markers(
                user_input,
                session_key,
                state.get("session_manager"),
                ctx,
                notify=_fb_file_notify,
            )

            _fb_coordinator.begin_turn(session_key, source="cli")
            cli_append = _fb_coordinator.make_session_append(session_key)
            cli_append_ansi = _fb_coordinator.make_session_append_ansi(session_key)
            # 整轮 turn 纳入会话级串行边界（见主终端 _process_input 说明）。
            async with engine.session_turn(session_key):
                try:
                    _fallback_print_locked("\n\n")
                    _fb_rule_heavy()
                    format_cli_user_block(cli_append, user_input, stick)
                    reply = await engine.run_agent_with_thinking(
                        user_input,
                        session_key,
                        _skill_tb(),
                        _skill_sp(),
                        registry=registry,
                        monitor=monitor,
                        session_manager=state.get("session_manager"),
                        channel_router=channel_router,
                        clawhub=ctx.clawhub,
                        memory_store=ctx.memory_store,
                        activity_log=ctx.activity_log,
                        keyword_index=ctx.keyword_index,
                        memory_context=ctx.memory_context,
                        client=ctx.openai_client,
                        cli_loop_state=state,
                        _hold_session_lock=True,
                    )
                    if reply and reply.strip():
                        format_cli_reply_block(cli_append, cli_append_ansi, (reply or "").strip())
                finally:
                    _fb_coordinator.end_turn(session_key)
        except Exception as e:
            _fallback_print_locked(f"{ERROR_PREFIX} 错误: {e}\n")

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\n❯ ")
        except (EOFError, KeyboardInterrupt):
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        if user_input.startswith("!"):
            bash_cmd = user_input[1:].strip()
            if bash_cmd:
                _ok, output = run_cli_bash_command(bash_cmd)
                _fallback_print_locked(output)
            continue

        if user_input == "/copy":
            from miniagent.engine.cli_commands import build_session_history_plaintext

            plain = build_session_history_plaintext(
                state.get("session_manager"),
                state.get("active_session_id", ""),
            )
            if plain and copy_text_to_system_clipboard(plain):
                print(f"\n{SUCCESS_PREFIX} 已复制 {len(plain)} 字符到剪贴板\n")
            elif plain:
                print(f"\n{ERROR_PREFIX} 复制失败（无剪贴板或缺少 wl-copy / xclip / pbcopy / clip）\n")
            else:
                print(
                    "\n提示: 当前会话无历史可复制；全屏 CLI 下 /copy 复制 transcript。\n"
                )
            continue

        if user_input == "/stop":
            await shutdown_runtime(
                ctx,
                state,
                reason="dot_stop_fallback",
                release_cli_session_lock=True,
                call_unregister=True,
            )
            print(f"{SUCCESS_PREFIX} 当前实例已停止")
            break

        # 其余 ``/`` 命令：与全屏 TUI 一致走 ``dispatch_command``（stdout 输出）
        if user_input.startswith("/"):
            from miniagent.engine.command_dispatch import dispatch_command

            prev_session_id = state["active_session_id"]
            result = await dispatch_command(
                user_input,
                state=state,
                engine=engine,
                registry=registry,
                monitor=monitor,
                skill_toolboxes=_skill_tb(),
                skill_prompts=get_skill_prompts_from_state(state) or skill_prompts,
                capture=False,
                allow_session_mutations_when_capture=True,
                feishu_user_status=_feishu_user_status_fn(ctx),
            )
            if state["active_session_id"] != prev_session_id:
                _fb_show_session_history("\n📜 已切换会话，最近历史如下：\n")
                _prime_fallback_readline_history(history_file)
            if result == "__EXIT__":
                break
            if result is not None:
                print(result)
            continue

        # ── 需求澄清追问拦截：普通消息自动注入为回答 ──
        from miniagent.engine.parallel_config import resolve_active_session_key

        active_sk = resolve_active_session_key(
            channel_router, state.get("active_session_id") or "default"
        )
        engine.set_active_session_key(active_sk)
        cc = engine.get_confirmation_channel(active_sk)
        if cc and cc.has_pending:
            from miniagent.types.confirmation import ConfirmationResult, ConfirmationStage

            if cc.pending.stage == ConfirmationStage.CLARIFICATION:
                cc.respond(ConfirmationResult.clarification_reply(user_input))
                continue

        await message_queue.dispatch_cli(_process_input(user_input))

        if readline is not None:
            try:
                readline.write_history_file(history_file)
            except Exception:
                pass

        try:
            heartbeat()
        except Exception:
            pass

    from miniagent.engine.session_continue import save_cli_session_state

    # 保存 CLI 上次会话状态（--continue 功能）
    save_cli_session_state(ctx, state)

    # 清理思考显示回调（Linux 兼容性）
    engine.thinking.set_output_sink(None)
    ctx.cli_transcript_append = None

    release_session_lock(state["active_session_id"])
    try:
        unregister_instance()
    except Exception:
        pass
    print("\n\U0001f44b bye")



__all__ = ["unified_main", "run_cli_loop"]
