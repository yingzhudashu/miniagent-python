"""Engine — 主启动入口

职责：
- 信号处理注册
- 子系统初始化
- 装配 CLI TUI/fallback surface；可选同进程内启动飞书长轮询（无独立「纯飞书」入口）
- 优雅关闭（含子进程清理）
- 子进程清理（``cleanup_all_processes``）

依赖注入：``run_runtime`` 与飞书 handler 工厂通过
:class:`miniagent.bootstrap.application.ApplicationContainer` 获取 registry、monitor、engine 等，
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

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.cli_tui import run_cli_loop
from miniagent.engine.feishu_handler import create_feishu_handler
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.engine.utils import feishu_user_status_fn as _feishu_user_status_fn

# 飞书状态行输出（用于 feishu.start() 的 user_status 参数）
from miniagent.infrastructure.instance import (
    ProjectDirConflictError,
    format_project_conflict_message,
    register_instance,
)
from miniagent.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)




def _configure_console_encoding() -> None:
    """在 Windows 平台将 stdout/stderr 设为 UTF-8，避免中文编码异常。"""
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                reconfigure(encoding="utf-8", errors="replace")


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
    registry = ctx.registry
    skill_registry = ctx.skill_registry
    engine = ctx.engine
    _configure_console_encoding()

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
        _logger.debug(
            "Windows VT模式设置失败（降级到prompt_toolkit）: %s", e
        )  # VT 模式不可用，降级到 prompt_toolkit 颜色

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
            _logger.debug(
                "信号关闭过程中异常（不影响退出）: %s", e
            )  # 关闭过程中的异常不影响最终退出
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

    cli_returned = False
    try:
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
            SessionManager,
            ctx.channel_router,
            clawhub=ctx.clawhub,
            keyword_index=ctx.memory.keyword_index,
        )
        state["active_session_id"] = active_session_id
        state["skill_toolboxes"] = skill_toolboxes
        state["skill_prompts"] = skill_prompts
        state["session_manager"] = session_manager

        from miniagent.engine.parallel_config import configure_message_queue_for_parallel

        configure_message_queue_for_parallel(ctx.message_queue)
        engine.set_active_session_key(active_session_id)

        from miniagent.bootstrap.runtime_services import build_runtime_lifecycle_manager

        lifecycle_manager = build_runtime_lifecycle_manager(
            ctx,
            state,
            skill_toolboxes,
            skill_prompts,
            feishu_user_status=_feishu_user_status_fn(ctx),
        )
        ctx.lifecycle_manager = lifecycle_manager
        await lifecycle_manager.start()

        print_welcome(
            registry,
            skill_registry,
            MODEL,
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
