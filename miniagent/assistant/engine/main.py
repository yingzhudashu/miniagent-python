"""Engine — 主启动入口

职责：
- 信号处理注册
- 子系统初始化
- 装配 CLI TUI/fallback surface；可选同进程内启动飞书长轮询（无独立「纯飞书」入口）
- 优雅关闭（含子进程清理）
- 子进程清理（``cleanup_all_processes``）

依赖注入：``run_runtime`` 与飞书 handler 工厂通过
:class:`miniagent.assistant.bootstrap.application.ApplicationContainer` 获取 registry、monitor、engine 等，
所有运行时依赖均由入口显式构造和传递。

异步时序（队列 → Agent → 回复）见 ``docs/ARCHITECTURE.md``；点命令见 ``docs/CLI.md``。

CLI TUI、fallback、历史、文件摄取、shell 执行和入站/出站适配均位于独立模块；
本文件只负责进程注册、信号、子系统初始化、生命周期启动和统一关停。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # prompt_toolkit≥3.0.50 仅在类型检查块中定义该别名，运行时 key_bindings 无此名（勿在运行中 from … import）。
    pass

from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.cli_tui import run_cli_loop
from miniagent.assistant.engine.feishu_handler import create_feishu_handler
from miniagent.assistant.engine.shutdown import shutdown_runtime
from miniagent.assistant.engine.utils import feishu_user_status_fn as _feishu_user_status_fn

# 飞书状态行输出（用于 feishu.start() 的 user_status 参数）
from miniagent.assistant.infrastructure.instance import (
    ProjectDirConflictError,
    format_project_conflict_message,
    register_instance,
)
from miniagent.assistant.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)




def _configure_console_encoding() -> None:
    """在 Windows 平台将 stdout/stderr 设为 UTF-8，避免中文编码异常。"""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")


def _enable_windows_vt() -> None:
    """尽力启用 Windows VT；失败时保留 prompt_toolkit 降级。"""
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        if handle and handle != -1:
            mode = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception as error:
        _logger.debug("Windows VT模式设置失败（降级到prompt_toolkit）: %s", error)


def _initial_runtime_state(ctx: ApplicationContainer, feishu_mode: bool) -> CliLoopState:
    """注册进程实例并构造显式 CLI 运行状态。"""
    try:
        registration = register_instance(
            mode="both" if feishu_mode else "cli", active_sessions=[]
        )
    except ProjectDirConflictError as error:
        print(format_project_conflict_message(error.existing_meta))
        raise SystemExit(2) from error
    return {
        "active_session_id": "",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": feishu_mode,
        "session_manager": None,
        "instance_id": registration.get("instance_id", 0),
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }


def _install_signal_shutdown(ctx: ApplicationContainer, state: CliLoopState) -> None:
    """将 SIGINT/SIGTERM 安全桥接到事件循环内的统一关停协程。"""
    loop = asyncio.get_running_loop()
    signal_lock = threading.Lock()
    armed = {"value": False}

    async def shutdown_after_signal(signum: int) -> None:
        try:
            await shutdown_runtime(ctx, state, reason=f"signal:{signum}", call_unregister=True)
        except Exception as error:
            _logger.debug("信号关闭过程中异常（不影响退出）: %s", error)
        os._exit(0)

    def on_exit(signum: int, *_: Any) -> None:
        with signal_lock:
            if armed["value"]:
                os._exit(128)
            armed["value"] = True

        def kick() -> None:
            asyncio.create_task(shutdown_after_signal(signum))

        loop.call_soon_threadsafe(kick)

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)


async def _start_runtime_services(
    ctx: ApplicationContainer, state: CliLoopState
) -> tuple[list[Any], list[Any], str]:
    """初始化会话、技能、并行队列与生命周期服务。"""
    from miniagent.assistant.engine.init import init_subsystems
    from miniagent.assistant.session.manager import DefaultSessionManager as SessionManager

    _, skill_toolboxes, skill_prompts, active_session_id, session_manager = (
        await init_subsystems(
            ctx.registry,
            ctx.skill_registry,
            SessionManager,
            ctx.channel_router,
            clawhub=ctx.clawhub,
            keyword_index=ctx.memory.keyword_index,
        )
    )
    state["active_session_id"] = active_session_id
    state["skill_toolboxes"] = skill_toolboxes
    state["skill_prompts"] = skill_prompts
    state["session_manager"] = session_manager
    from miniagent.assistant.engine.parallel_config import configure_message_queue_for_parallel

    configure_message_queue_for_parallel(ctx.message_queue)
    ctx.engine.set_active_session_key(active_session_id)
    from miniagent.assistant.bootstrap.runtime_services import build_runtime_lifecycle_manager

    manager = build_runtime_lifecycle_manager(
        ctx,
        state,
        skill_toolboxes,
        skill_prompts,
        feishu_user_status=_feishu_user_status_fn(ctx),
    )
    ctx.lifecycle_manager = manager
    await manager.start()
    return skill_toolboxes, skill_prompts, active_session_id


# ─── run_runtime：组合根注入后的进程主流程（init → 信号/实例 → CLI / 飞书）──


async def run_runtime(ctx: ApplicationContainer) -> None:
    """主启动流程。

    不再检查全局单实例 — 支持多实例并行。
    每个实例通过会话级 .lock 文件隔离。

    嵌入场景若不经正式入口，调用方须先
    ``load_secrets_from_project_root()`` 或预先设置 ``OPENAI_*`` 等敏感凭据环境变量。

    Args:
        ctx: 运行时组合根（registry / monitor / skill_registry / clawhub / engine）
    """
    _configure_console_encoding()
    _enable_windows_vt()
    model = get_config("model.model", "gpt-4o-mini")
    from miniagent.assistant.engine.welcome import print_welcome
    feishu_mode = "--feishu" in sys.argv
    state = _initial_runtime_state(ctx, feishu_mode)
    _dummy_stick: list[bool] = [True]
    ctx.create_feishu_handler_factory = lambda st: create_feishu_handler(st, ctx, _dummy_stick)
    _install_signal_shutdown(ctx, state)

    cli_returned = False
    try:
        skill_toolboxes, skill_prompts, active_session_id = await _start_runtime_services(ctx, state)
        print_welcome(
            ctx.registry,
            ctx.skill_registry,
            model,
            state.get("session_manager"),
            active_session_id,
            state["feishu_enabled"],
        )

        await run_cli_loop(
            ctx,
            state,
            skill_toolboxes,
            skill_prompts,
        )
        cli_returned = True
    finally:
        # ``run_cli_loop`` normally releases the CLI session lock and instance
        # registration itself. Startup and exceptional paths have not done so.
        await shutdown_runtime(
            ctx,
            state,
            reason="run_cli_loop_returned" if cli_returned else "run_runtime_finally",
            abort_message_queues=True,
            release_cli_session_lock=not cli_returned,
            call_unregister=not cli_returned,
        )


__all__ = ["run_runtime"]
