"""飞书运行时状态 — 每进程一个实例，由 ``ApplicationContainer`` 持有。

封装原 ``feishu_runtime`` 模块级全局（task / config / running），便于测试与多上下文隔离。

协议细节与运维配置见 ``docs/FEISHU.md``。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# ``create_handler(state)`` 返回文本 handler，或 ``(text_handler, media_handler)`` 元组。
FeishuHandlerFactory = Callable[
    [dict[str, Any] | None],
    Callable[..., Awaitable[Any]] | tuple[Any, Any],
]


class FeishuRuntime:
    """飞书 WebSocket 长轮询生命周期（绑定到特定 :class:`~miniagent.infrastructure.message_queue.MessageQueueManager`）。

    **关停**：优先 ``await stop_async()``（``shutdown_runtime``、CLI ``/feishu stop``）；
    同步 ``stop()`` 仅 ``cancel`` 后台 task，入站锁与实例连接状态在 task ``finally`` 中释放。
    """

    def __init__(self, message_queue: Any) -> None:
        """Args:
        message_queue: 与 CLI 共用的 :class:`~miniagent.infrastructure.message_queue.MessageQueueManager`。
        """
        self._message_queue = message_queue
        self._task: asyncio.Task | None = None
        self._running: bool = False
        self._config: Any = None
        self._user_status: Callable[[str], None] | None = None
        self._poll_state: Any | None = None

    def _ensure_poll_state(self) -> Any:
        """Construct the heavy Feishu SDK-backed state only when Feishu starts."""
        if self._poll_state is None:
            from miniagent.feishu.poll_server import FeishuPollState

            self._poll_state = FeishuPollState()
        return self._poll_state

    def _emit_user_line(self, msg: str) -> None:
        """用户可见状态行：优先走全屏 CLI transcript，否则 stdout。"""
        if self._user_status:
            self._user_status(msg)
        else:
            print(msg, flush=True)

    def _on_runtime_task_done(self, completed: asyncio.Task[Any]) -> None:
        """Consume terminal exceptions and release the runtime task reference."""
        if self._task is completed:
            self._task = None
        self._running = False
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            _logger.error("飞书后台任务异常退出: %s", error, exc_info=error)

    @staticmethod
    def _instance_id(state: dict[str, Any] | None) -> int | None:
        """从 CLI 状态解析可选实例 ID。"""
        if not isinstance(state, dict):
            return None
        try:
            return int(state.get("instance_id") or 0) or None
        except (TypeError, ValueError):
            return None

    async def _run_poll_loop(
        self,
        config: Any,
        text_handler: Any,
        media_handler: Any,
        poll_state: Any,
    ) -> None:
        """带指数退避维持 WebSocket；退出时释放状态与入站锁。"""
        from miniagent.feishu.im_tool_policy import log_feishu_im_tools_startup_hint_once
        from miniagent.feishu.poll_server import start_feishu_poll_server
        from miniagent.infrastructure.feishu_inbound_lock import release_feishu_inbound_owner

        attempt = 0
        try:
            log_feishu_im_tools_startup_hint_once()
            self._emit_user_line("🌐 [飞书] 正在启动 WebSocket 长轮询…")
            while True:
                if attempt:
                    cap = min(60.0, 2.0 ** min(attempt, 6))
                    delay = cap * (0.5 + random.random() * 0.5)
                    self._emit_user_line(f"ℹ️ [飞书] 约 {delay:.1f}s 后重连…")
                    await asyncio.sleep(delay)
                try:
                    await poll_state.reset()
                    await start_feishu_poll_server(
                        config,
                        text_handler,
                        runtime_state=poll_state,
                        message_queue=self._message_queue,
                        media_handler=media_handler,
                    )
                    end_reason, _ = poll_state.ws_health.last_session_end()
                    if end_reason:
                        self._emit_user_line(f"ℹ️ [飞书] 会话结束（{end_reason}），将重连…")
                except asyncio.CancelledError:
                    self._emit_user_line("ℹ️ [飞书] 已停止")
                    raise
                except Exception as error:
                    _logger.error("[飞书] 运行异常: %s", error, exc_info=True)
                    self._emit_user_line(f"ℹ️ [飞书] 连接异常，将重试: {error}")
                attempt += 1
        finally:
            try:
                await poll_state.reset()
            except Exception as error:
                _logger.debug("重置飞书连接状态失败（清理路径）: %s", error, exc_info=True)
            try:
                release_feishu_inbound_owner()
            except Exception as error:
                _logger.debug("释放入站锁失败（清理路径）: %s", error, exc_info=True)
            self._task = None
            self._running = False

    @staticmethod
    def _build_start_config() -> Any:
        """从环境变量构造飞书启动配置。"""
        from miniagent.feishu.types import FeishuConfig

        return FeishuConfig(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
        )

    @staticmethod
    def _bind_confirmation_runtime(poll_state: Any, state: dict | None) -> None:
        """把组合根确认引擎与路由器绑定到轮询状态。"""
        if not isinstance(state, dict):
            return
        runtime = state.get("runtime_ctx")
        engine = getattr(runtime, "engine", None) if runtime else None
        router = getattr(runtime, "channel_router", None) if runtime else None
        if engine is not None:
            poll_state.bind_confirmation(engine, router)

    @staticmethod
    def _report_started_mode() -> None:
        """记录远程变异命令开关，并更新实例运行模式。"""
        try:
            from miniagent.engine.cli_commands import feishu_dot_commands_full_enabled

            if feishu_dot_commands_full_enabled():
                _logger.warning(
                    "已启用 MINIAGENT_FEISHU_DOT_COMMANDS_FULL：飞书可使用全部命令"
                    "（含 /session/.schedule 变异与 /stop，会修改与 CLI 共享的状态）"
                )
        except Exception as error:
            _logger.debug("检查飞书点命令权限失败（非关键）: %s", error)
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("both")
        except Exception as error:
            _logger.debug("更新实例模式失败（非关键）: %s", error)

    def start(
        self,
        create_handler: FeishuHandlerFactory,
        state: dict | None = None,
        *,
        user_status: Callable[[str], None] | None = None,
    ) -> None:
        """启动飞书 WebSocket 长轮询后台任务。

        读取环境变量 ``FEISHU_APP_ID``、``FEISHU_APP_SECRET``、``FEISHU_VERIFICATION_TOKEN``
        构造配置；无 ``FEISHU_APP_ID`` 时跳过启动。

        用户可见的「✅ 飞书已启动」表示后台 :class:`asyncio.Task` 已创建，**不表示**
        WebSocket 已连通；连接进度由 ``_run`` 内后续状态行反馈。

        Args:
            create_handler: ``(state) -> handler`` 或 ``(state) -> (text_h, media_h)``；
                典型为 ``ApplicationContainer.create_feishu_handler_factory``。
            state: CLI 循环状态；``start`` 会读取：

                - ``runtime_ctx``：绑定确认引擎与通道路由
                - ``instance_id``：写入飞书入站独占锁

            user_status: 可选 ``(msg: str) -> None``，由全屏 CLI 注册为写入 transcript；
                未提供时使用 ``print``，避免与 prompt_toolkit 备用屏混写时丢失信息。
        """
        self._user_status = user_status
        config = self._build_start_config()

        if not config.app_id:
            _logger.warning("未配置 FEISHU_APP_ID，跳过飞书启动")
            self._emit_user_line(
                "\u274c \u672a\u914d\u7f6e\u98de\u4e66\u51ed\u8bc1 (FEISHU_APP_ID)"
            )
            return

        poll_state = self._ensure_poll_state()
        self._bind_confirmation_runtime(poll_state, state)

        if self._running and self._task and not self._task.done():
            self._emit_user_line("\u2139\ufe0f [\u98de\u4e66] \u5df2\u5728\u8fd0\u884c\u4e2d")
            return

        from miniagent.infrastructure.feishu_inbound_lock import (
            try_acquire_feishu_inbound_owner,
        )

        ok, lock_msg = try_acquire_feishu_inbound_owner(instance_id=self._instance_id(state))
        if not ok:
            self._emit_user_line(lock_msg)
            return

        from miniagent.infrastructure.feishu_inbound_lock import (
            release_feishu_inbound_owner,
        )

        try:
            self._config = config
            h = create_handler(state)
        except Exception as e:
            release_feishu_inbound_owner()
            self._emit_user_line(f"\u274c \u98de\u4e66\u542f\u52a8\u5931\u8d25: {e}")
            raise

        if isinstance(h, tuple):
            text_h = h[0]
            media_h = h[1] if len(h) > 1 else None
        else:
            text_h, media_h = h, None
        self._task = asyncio.create_task(self._run_poll_loop(config, text_h, media_h, poll_state))
        self._task.add_done_callback(self._on_runtime_task_done)
        self._running = True
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u542f\u52a8")
        self._report_started_mode()

    async def stop_async(self) -> None:
        """异步停止（推荐）：等待后台 task 完成取消链。

        顺序：向 ``FeishuPollState`` 发出 shutdown → 等待 task（最多 3s，超时 ``cancel``）
        → task ``finally`` 内 ``FeishuPollState.reset`` / ``release_feishu_inbound_owner``。
        """
        t = self._task
        if not self._running and not (t and not t.done()):
            try:
                from miniagent.infrastructure.feishu_inbound_lock import (
                    release_feishu_inbound_owner,
                )

                release_feishu_inbound_owner()
            except Exception as e:
                _logger.debug("释放锁失败（停止路径）: %s", e)
            return

        try:
            if self._poll_state is not None:
                self._poll_state.request_shutdown()
        except Exception as e:
            _logger.debug("请求WS关闭失败（停止路径）: %s", e)
        if t and not t.done():
            try:
                await asyncio.wait_for(t, timeout=3.0)
            except asyncio.TimeoutError:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError as e:
                        _logger.debug(
                            "\u7b49\u5f85\u98de\u4e66\u4efb\u52a1\u53d6\u6d88\u5931\u8d25\uff08\u6e05\u7406\u8def\u5f84\uff09: %s",
                            e,
                        )
            except asyncio.CancelledError as e:
                _logger.debug(
                    "\u7b49\u5f85\u98de\u4e66\u4efb\u52a1\u8d85\u65f6\u5931\u8d25\uff08\u5df2\u53d6\u6d88\uff09: %s",
                    e,
                )
        self._task = None
        self._running = False
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u505c\u6b62")
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("cli")
        except Exception as e:
            _logger.debug(
                "\u66f4\u65b0\u5b9e\u4f8b\u6a21\u5f0f\u5931\u8d25\uff08\u505c\u6b62\u8def\u5f84\uff09: %s",
                e,
            )

    def stop(self) -> None:
        """同步停止（fire-and-forget）：``cancel`` 后台 task，不 ``await``。

        入站锁与实例连接状态在 task ``finally`` 中释放；无法等待清理完成时请用 ``stop_async``。
        未运行时会防御性尝试释放本进程持有的入站锁。
        """
        if not self._running:
            self._emit_user_line("\u2139\ufe0f \u98de\u4e66\u672a\u8fd0\u884c")
            try:
                from miniagent.infrastructure.feishu_inbound_lock import (
                    release_feishu_inbound_owner,
                )

                release_feishu_inbound_owner()
            except Exception as e:
                _logger.debug("释放锁失败（停止路径）: %s", e)
            return

        self._running = False
        t = self._task
        try:
            if self._poll_state is not None:
                self._poll_state.request_shutdown()
        except Exception as e:
            _logger.debug("请求WS关闭失败（停止路径）: %s", e)
        if t and not t.done():
            t.cancel()

            def _clear(_fut: asyncio.Task) -> None:
                """清理任务引用回调。"""
                if self._task is t:
                    self._task = None

            t.add_done_callback(_clear)
        else:
            self._task = None
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u505c\u6b62")
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("cli")
        except Exception as e:
            _logger.debug(
                "\u66f4\u65b0\u5b9e\u4f8b\u6a21\u5f0f\u5931\u8d25\uff08\u505c\u6b62\u8def\u5f84\uff09: %s",
                e,
            )

    def status(self) -> None:
        """输出飞书状态（经 user_status 或 print）。"""
        if self._running:
            self._emit_user_line("\U0001f7e2 \u98de\u4e66: \u8fd0\u884c\u4e2d")
        else:
            self._emit_user_line("\u26aa \u98de\u4e66: \u672a\u542f\u7528")
        try:
            if self._poll_state is None:
                raise LookupError("Feishu poll state has not been initialized")
            end_reason, end_at = self._poll_state.ws_health.last_session_end()
            if end_reason:
                when = ""
                if end_at:
                    when = time.strftime("%H:%M:%S", time.localtime(end_at))
                self._emit_user_line(
                    f"\U0001f4ca \u98de\u4e66 WS: \u4e0a\u6b21\u4f1a\u8bdd\u7ed3\u675f={end_reason}"
                    + (f" ({when})" if when else "")
                )
            last_in = self._poll_state.ws_health.last_inbound_monotonic
            if last_in is not None:
                ago = time.monotonic() - last_in
                self._emit_user_line(
                    f"\U0001f4ca \u98de\u4e66 WS: \u6700\u540e\u5165\u7ad9\u7ea6 {ago:.0f}s \u524d"
                )
        except Exception as e:
            _logger.debug("读取飞书 WS 健康状态失败（非关键）: %s", e)
        try:
            from miniagent.infrastructure.feishu_inbound_lock import (
                read_feishu_inbound_owner,
            )

            info = read_feishu_inbound_owner()
            if info:
                alive = info.get("alive")
                pid = info.get("pid")
                oid = info.get("instance_id", "?")
                st = "\u5b58\u6d3b" if alive else "\u53ef\u80fd\u5df2\u6b7b"
                self._emit_user_line(
                    f"\U0001f512 \u98de\u4e66\u5165\u7ad9\u9501: PID={pid} "
                    f"\u5b9e\u4f8b#{oid} ({st})"
                )
            else:
                self._emit_user_line(
                    "\U0001f513 \u98de\u4e66\u5165\u7ad9\u9501: \u672a\u5360\u7528"
                )
        except Exception as e:
            _logger.debug("读取飞书入站锁状态失败（非关键）: %s", e)

    def is_running(self) -> bool:
        """是否标记为运行中（与底层 task 是否存活可能短暂不一致）。"""
        return self._running

    def get_config(self) -> Any:
        """返回最近一次构造的 :class:`~miniagent.feishu.types.FeishuConfig` 或 ``None``。"""
        return self._config

    def get_task(self) -> asyncio.Task | None:
        """返回飞书长轮询后台 :class:`asyncio.Task`，未启动则为 ``None``。"""
        return self._task

    def set_task(self, task: asyncio.Task | None) -> None:
        """测试或特殊场景下替换后台 task 句柄。"""
        self._task = task

    def set_running(self, value: bool) -> None:
        """直接设置运行标志（慎用；正常路径由 ``start``/``stop``/``_run`` 维护）。"""
        self._running = value

    def set_config(self, config: Any) -> None:
        """测试注入用：覆盖当前缓存的飞书配置对象。"""
        self._config = config


__all__ = ["FeishuHandlerFactory", "FeishuRuntime"]
