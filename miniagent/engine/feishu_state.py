"""飞书运行时状态 — 每进程一个实例，由 :class:`RuntimeContext` 持有。

封装原 ``feishu_runtime`` 模块级全局（task / config / running），便于测试与多上下文隔离。

协议细节与运维配置见 ``docs/FEISHU.md``。
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any, Callable

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


class FeishuRuntime:
    """飞书 WebSocket 长轮询生命周期（绑定到特定 :class:`~miniagent.infrastructure.message_queue.MessageQueueManager`）。"""

    def __init__(self, message_queue: Any) -> None:
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
        from miniagent.feishu.poll_server import reset_feishu_ws_singleton, start_feishu_poll_server
        from miniagent.feishu.types import FeishuConfig

        self._user_status = user_status

        config = FeishuConfig(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
        )

        if not config.app_id:
            _logger.warning("未配置 FEISHU_APP_ID，跳过飞书启动")
            self._emit_user_line("\u274c \u672a\u914d\u7f6e\u98de\u4e66\u51ed\u8bc1 (FEISHU_APP_ID)")
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
            attempt = 0
            try:
                _logger.info("\u98de\u4e66: \u6b63\u5728\u542f\u52a8 WebSocket \u957f\u8f6e\u8be2")
                self._emit_user_line("\U0001f310 [\u98de\u4e66] \u6b63\u5728\u542f\u52a8 WebSocket \u957f\u8f6e\u8be2\u2026")
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
                            _logger.info("\u98de\u4e66: \u5df2\u53d6\u6d88\uff08\u9000\u51fa\u7b49\u5f85\uff09")
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
                        _logger.warning(
                            "\u98de\u4e66 WebSocket \u4f1a\u8bdd\u5df2\u7ed3\u675f\uff0c\u5c06\u9000\u907f\u540e\u91cd\u8fde"
                        )
                    except asyncio.CancelledError:
                        _logger.info("\u98de\u4e66: \u5df2\u53d6\u6d88")
                        self._emit_user_line("\u2139\ufe0f [\u98de\u4e66] \u5df2\u505c\u6b62")
                        raise
                    except Exception as e:
                        _logger.error("[\u98de\u4e66] \u8fd0\u884c\u5f02\u5e38: %s", e, exc_info=True)
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
                self._running = False

        self._task = asyncio.create_task(_run())
        self._running = True
        self._emit_user_line("\u2705 \u98de\u4e66\u5df2\u542f\u52a8")
        try:
            from miniagent.infrastructure.instance import update_instance_mode

            update_instance_mode("both")
        except Exception:
            pass

    def stop(self) -> None:
        """停止飞书连接。"""
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
        if self._task:
            self._task.cancel()
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
        return self._running

    def get_config(self) -> Any:
        return self._config

    def get_task(self) -> asyncio.Task | None:
        return self._task

    def set_task(self, task: asyncio.Task | None) -> None:
        self._task = task

    def set_running(self, value: bool) -> None:
        self._running = value

    def set_config(self, config: Any) -> None:
        self._config = config


__all__ = ["FeishuRuntime"]
