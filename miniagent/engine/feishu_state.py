"""飞书运行时状态 — 每进程一个实例，由 :class:`RuntimeContext` 持有。

封装原 ``feishu_runtime`` 模块级全局（task / config / running），便于测试与多上下文隔离。

协议细节与运维配置见 ``docs/FEISHU.md``。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Callable
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


class FeishuRuntime:
    """飞书 WebSocket 长轮询生命周期（绑定到特定 :class:`~miniagent.infrastructure.message_queue.MessageQueueManager`）。"""

    def __init__(self, message_queue: Any) -> None:
        """Args:
        message_queue: 与 CLI 共用的 :class:`~miniagent.infrastructure.message_queue.MessageQueueManager`。
        """
        self._message_queue = message_queue
        self._task: asyncio.Task | None = None
        self._running: bool = False
        self._config: Any = None
        self._user_status: Callable[[str], None] | None = None

    def _emit_user_line(self, msg: str) -> None:
        """用户可见状态行：优先走全屏 CLI transcript，否则 stdout。"""
        if self._user_status:
            self._user_status(msg)
        else:
            print(msg, flush=True)

    def start(
        self,
        skill_toolboxes: list,
        skill_prompts: list,
        create_handler: Callable,
        state: dict | None = None,
        *,
        user_status: Callable[[str], None] | None = None,
    ) -> None:
        """启动飞书 WebSocket 长轮询。

        Args:
            user_status: 可选 ``(msg: str) -> None``，由全屏 CLI 注册为写入 transcript；
                未提供时使用 ``print``，避免与 prompt_toolkit 备用屏混写时丢失信息。
        """
        from miniagent.feishu.poll_server import (
            reset_feishu_ws_singleton,
            set_feishu_confirmation_engine,
            start_feishu_poll_server,
        )
        from miniagent.feishu.types import FeishuConfig

        self._user_status = user_status

        # 设置引擎引用，供卡片确认按钮直接响应确认通道
        if state is not None and isinstance(state, dict):
            rt = state.get("runtime_ctx")
            engine = getattr(rt, "engine", None) if rt else None
            if engine is not None:
                set_feishu_confirmation_engine(engine)

        config = FeishuConfig(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
        )

        if not config.app_id:
            # #region agent log
            try:
                from miniagent.infrastructure.debug_ndjson import agent_debug_log

                agent_debug_log(
                    hypothesis_id="D",
                    location="feishu_state.py:FeishuRuntime.start",
                    message="feishu_skip_no_app_id",
                    data={},
                )
            except Exception:
                pass
            # #endregion
            _logger.warning("未配置 FEISHU_APP_ID，跳过飞书启动")
            self._emit_user_line(
                "\u274c \u672a\u914d\u7f6e\u98de\u4e66\u51ed\u8bc1 (FEISHU_APP_ID)"
            )
            return

        if self._running and self._task and not self._task.done():
            self._emit_user_line("\u2139\ufe0f [\u98de\u4e66] \u5df2\u5728\u8fd0\u884c\u4e2d")
            return

        from miniagent.infrastructure.feishu_inbound_lock import (
            try_acquire_feishu_inbound_owner,
        )

        inst_id = None
        if state is not None and isinstance(state, dict):
            try:
                inst_id = int(state.get("instance_id") or 0) or None
            except (TypeError, ValueError):
                inst_id = None
        ok, lock_msg = try_acquire_feishu_inbound_owner(instance_id=inst_id)
        # #region agent log
        try:
            from miniagent.infrastructure.debug_ndjson import agent_debug_log

            agent_debug_log(
                hypothesis_id="D",
                location="feishu_state.py:FeishuRuntime.start",
                message="feishu_inbound_lock",
                data={
                    "lock_ok": ok,
                    "instance_id": inst_id,
                    "lock_msg_snip": (lock_msg or "")[:200],
                },
            )
        except Exception:
            pass
        # #endregion
        if not ok:
            self._emit_user_line(lock_msg)
            return

        from miniagent.infrastructure.feishu_inbound_lock import (
            release_feishu_inbound_owner,
        )

        try:
            self._config = config
            h = (
                create_handler(skill_toolboxes, skill_prompts, state)
                if state is not None
                else create_handler(skill_toolboxes, skill_prompts)
            )
        except Exception as e:
            release_feishu_inbound_owner()
            self._emit_user_line(f"\u274c \u98de\u4e66\u542f\u52a8\u5931\u8d25: {e}")
            raise

        mq = self._message_queue
        if isinstance(h, tuple):
            text_h = h[0]
            media_h = h[1] if len(h) > 1 else None
        else:
            text_h, media_h = h, None

        async def _run() -> None:
            """后台协程：带指数退避地维持飞书 WebSocket 长轮询，退出时释放入站锁。"""
            attempt = 0
            try:
                from miniagent.feishu.im_tool_policy import log_feishu_im_tools_startup_hint_once

                log_feishu_im_tools_startup_hint_once()
                _logger.info("\u98de\u4e66: \u6b63\u5728\u542f\u52a8 WebSocket \u957f\u8f6e\u8be2")
                self._emit_user_line(
                    "\U0001f310 [\u98de\u4e66] \u6b63\u5728\u542f\u52a8 WebSocket \u957f\u8f6e\u8be2\u2026"
                )
                while True:
                    if attempt >= 1:
                        cap = min(60.0, 2.0 ** min(attempt, 6))
                        delay = cap * (0.5 + random.random() * 0.5)
                        self._emit_user_line(
                            f"\u2139\ufe0f [\u98de\u4e66] \u7ea6 {delay:.1f}s \u540e\u91cd\u8fde\u2026"
                        )
                        try:
                            await asyncio.sleep(delay)
                        except asyncio.CancelledError:
                            _logger.info(
                                "\u98de\u4e66: \u5df2\u53d6\u6d88\uff08\u9000\u51fa\u7b49\u5f85\uff09"
                            )
                            self._emit_user_line("\u2139\ufe0f [\u98de\u4e66] \u5df2\u505c\u6b62")
                            raise
                    try:
                        await reset_feishu_ws_singleton()
                        await start_feishu_poll_server(
                            config,
                            text_h,
                            message_queue=mq,
                            media_handler=media_h,
                        )
                        try:
                            from miniagent.feishu.ws_health import get_last_ws_session_end

                            end_reason, _ = get_last_ws_session_end()
                            if end_reason:
                                self._emit_user_line(
                                    f"\u2139\ufe0f [\u98de\u4e66] \u4f1a\u8bdd\u7ed3\u675f"
                                    f"\uff08{end_reason}\uff09\uff0c\u5c06\u91cd\u8fde\u2026"
                                )
                        except Exception:
                            pass
                    except asyncio.CancelledError:
                        _logger.info("\u98de\u4e66: \u5df2\u53d6\u6d88")
                        self._emit_user_line("\u2139\ufe0f [\u98de\u4e66] \u5df2\u505c\u6b62")
                        raise
                    except Exception as e:
                        _logger.error(
                            "[\u98de\u4e66] \u8fd0\u884c\u5f02\u5e38: %s", e
                        )
                        self._emit_user_line(
                            f"\u2139\ufe0f [\u98de\u4e66] \u8fde\u63a5\u5f02\u5e38\uff0c\u5c06\u91cd\u8bd5: {e}"
                        )
                    attempt += 1
            finally:
                try:
                    await reset_feishu_ws_singleton()
                except Exception:
                    pass
                try:
                    release_feishu_inbound_owner()
                except Exception:
                    pass
                self._task = None
                self._running = False

        self._task = asyncio.create_task(_run())
        self._running = True
        # #region agent log
        try:
            from miniagent.infrastructure.debug_ndjson import agent_debug_log

            agent_debug_log(
                hypothesis_id="D",
                location="feishu_state.py:FeishuRuntime.start",
                message="feishu_background_task_created",
                data={
                    "app_id_len": len(config.app_id or ""),
                    "has_secret": bool((config.app_secret or "").strip()),
                },
            )
        except Exception:
            pass
        # #endregion
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u542f\u52a8")
        try:
            from miniagent.engine.cli_commands import feishu_dot_commands_full_enabled

            if feishu_dot_commands_full_enabled():
                _logger.warning(
                    "已启用 MINIAGENT_FEISHU_DOT_COMMANDS_FULL：飞书可使用全部点命令"
                    "（含 .session/.schedule 变异与 .stop，会修改与 CLI 共享的状态）"
                )
        except Exception:
            pass
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("both")
        except Exception:
            pass

    async def stop_async(self) -> None:
        """异步停止：等待后台 task 完成取消链，以便 ``reset`` / 入站锁在 ``finally`` 中执行。"""
        t = self._task
        if not self._running and not (t and not t.done()):
            try:
                from miniagent.infrastructure.feishu_inbound_lock import (
                    release_feishu_inbound_owner,
                )

                release_feishu_inbound_owner()
            except Exception:
                pass
            return

        try:
            from miniagent.feishu.poll_server import request_feishu_ws_shutdown

            request_feishu_ws_shutdown()
        except Exception:
            pass
        if t and not t.done():
            try:
                await asyncio.wait_for(t, timeout=3.0)
            except asyncio.TimeoutError:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            except asyncio.CancelledError:
                pass
        self._task = None
        self._running = False
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u505c\u6b62")
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("cli")
        except Exception:
            pass

    def stop(self) -> None:
        """停止飞书连接（同步路径：仅 ``cancel``，清理在 task ``finally`` 与 ``stop_async`` 中完成）。"""
        if not self._running:
            self._emit_user_line("\u2139\ufe0f \u98de\u4e66\u672a\u8fd0\u884c")
            try:
                from miniagent.infrastructure.feishu_inbound_lock import (
                    release_feishu_inbound_owner,
                )

                release_feishu_inbound_owner()
            except Exception:
                pass
            return

        self._running = False
        t = self._task
        try:
            from miniagent.feishu.poll_server import request_feishu_ws_shutdown

            request_feishu_ws_shutdown()
        except Exception:
            pass
        if t and not t.done():
            t.cancel()

            def _clear(_fut: asyncio.Task) -> None:
                """清理任务引用回调。"""
                if self._task is t:
                    self._task = None

            t.add_done_callback(_clear)
        else:
            self._task = None
        try:
            from miniagent.infrastructure.feishu_inbound_lock import (
                release_feishu_inbound_owner,
            )

            release_feishu_inbound_owner()
        except Exception:
            pass
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u505c\u6b62")
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("cli")
        except Exception:
            pass

    def status(self) -> None:
        """输出飞书状态（经 user_status 或 print）。"""
        if self._running:
            self._emit_user_line("\U0001f7e2 \u98de\u4e66: \u8fd0\u884c\u4e2d")
        else:
            self._emit_user_line("\u26aa \u98de\u4e66: \u672a\u542f\u7528")
        try:
            from miniagent.feishu.ws_health import (
                get_last_ws_session_end,
                get_ws_last_inbound_monotonic,
            )

            end_reason, end_at = get_last_ws_session_end()
            if end_reason:
                when = ""
                if end_at:
                    when = time.strftime("%H:%M:%S", time.localtime(end_at))
                self._emit_user_line(
                    f"\U0001f4ca \u98de\u4e66 WS: \u4e0a\u6b21\u4f1a\u8bdd\u7ed3\u675f={end_reason}"
                    + (f" ({when})" if when else "")
                )
            last_in = get_ws_last_inbound_monotonic()
            if last_in is not None:
                ago = time.monotonic() - last_in
                self._emit_user_line(
                    f"\U0001f4ca \u98de\u4e66 WS: \u6700\u540e\u5165\u7ad9\u7ea6 {ago:.0f}s \u524d"
                )
        except Exception:
            pass
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
        except Exception:
            pass

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


__all__ = ["FeishuRuntime"]
