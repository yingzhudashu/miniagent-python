"""飞书 WebSocket 会话健康监督：看门狗、定期刷新、结束原因可观测。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from miniagent.feishu.ws_client import FeishuWsClient, feishu_ws_auto_reconnect_enabled
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


async def _cancel_and_consume(tasks: set[asyncio.Task[Any]]) -> None:
    """Cancel unfinished tasks and retrieve every terminal exception."""
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(slots=True)
class FeishuWsHealthState:
    """Health observations owned by one Feishu runtime connection loop."""

    last_inbound_monotonic: float | None = None
    last_session_end_reason: str | None = None
    last_session_end_at: float | None = None

    def touch_inbound(self) -> None:
        """Record activity from an inbound SDK callback."""
        self.last_inbound_monotonic = time.monotonic()

    def record_session_end(self, reason: str) -> None:
        """Record why and when the supervised WebSocket session ended."""
        self.last_session_end_reason = reason
        self.last_session_end_at = time.time()

    def last_session_end(self) -> tuple[str | None, float | None]:
        """Return ``(reason, ended_at_unix)`` for status reporting."""
        return self.last_session_end_reason, self.last_session_end_at


@dataclass(frozen=True)
class FeishuWsHealthConfig:
    """飞书长连接健康检查的不可变时间参数。"""

    watchdog_interval_s: float
    dead_conn_grace_s: float
    reconnect_grace_s: float
    refresh_interval_s: float
    idle_refresh_s: float


def read_feishu_ws_health_config() -> FeishuWsHealthConfig:
    """从用户配置读取并构造本次连接使用的健康检查参数。"""
    return FeishuWsHealthConfig(
        watchdog_interval_s=float(get_config("feishu.websocket.watchdog_interval", 30.0)),
        dead_conn_grace_s=float(get_config("feishu.websocket.dead_conn_grace", 90.0)),
        reconnect_grace_s=float(get_config("feishu.websocket.reconnect_grace", 300.0)),
        refresh_interval_s=float(get_config("feishu.websocket.refresh_interval", 0.0)),
        idle_refresh_s=float(get_config("feishu.websocket.idle_refresh", 0.0)),
    )


def _receive_loop_exit_reason(task: asyncio.Task[Any]) -> str:
    """从 asyncio Task 中提取 receive_loop 退出原因。"""
    if task.cancelled():
        return "receive_loop_cancelled"
    exc = task.exception()
    if exc is None:
        return "receive_loop_exit"
    return f"receive_loop_exit:{type(exc).__name__}"


async def _watchdog_loop(
    ws_client: FeishuWsClient,
    config: FeishuWsHealthConfig,
    session_start: float,
    shutdown_event: asyncio.Event,
    exit_event: asyncio.Event,
    reason_holder: list[str],
    health_state: FeishuWsHealthState,
) -> None:
    """监督连接存活、SDK 重连和主动刷新，并报告退出原因。"""
    dead_since: float | None = None
    reconnect_dead_since: float | None = None
    sdk_auto = feishu_ws_auto_reconnect_enabled()

    while not shutdown_event.is_set() and not exit_event.is_set():
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=config.watchdog_interval_s,
            )
            return
        except asyncio.TimeoutError:
            _logger.debug("看门狗等待超时，继续检查")

        if shutdown_event.is_set() or exit_event.is_set():
            return

        now = time.monotonic()

        if config.refresh_interval_s > 0 and (now - session_start) >= config.refresh_interval_s:
            reason_holder[0] = "watchdog_refresh"
            exit_event.set()
            return

        last_inbound = health_state.last_inbound_monotonic
        if config.idle_refresh_s > 0 and last_inbound is not None:
            if (now - last_inbound) >= config.idle_refresh_s:
                reason_holder[0] = "watchdog_idle_refresh"
                exit_event.set()
                return
        receive_task = ws_client.receive_task
        if receive_task is not None and receive_task.done():
            reason_holder[0] = _receive_loop_exit_reason(receive_task)
            exit_event.set()
            return

        if not ws_client.connected:
            if sdk_auto:
                if reconnect_dead_since is None:
                    reconnect_dead_since = now
                elif (now - reconnect_dead_since) >= config.reconnect_grace_s:
                    reason_holder[0] = "watchdog_reconnect_grace"
                    exit_event.set()
                    return
            else:
                if dead_since is None:
                    dead_since = now
                elif (now - dead_since) >= config.dead_conn_grace_s:
                    reason_holder[0] = "watchdog_dead_conn"
                    exit_event.set()
                    return
        else:
            dead_since = None
            reconnect_dead_since = None


async def supervise_feishu_ws_session(
    ws_client: FeishuWsClient,
    *,
    shutdown_event: asyncio.Event,
    health_state: FeishuWsHealthState,
) -> str:
    """监督 WebSocket 会话直至应结束；返回结束原因字符串。"""
    config = read_feishu_ws_health_config()
    session_start = time.monotonic()
    exit_event = asyncio.Event()
    reason_holder: list[str] = ["unknown"]

    receive_task = ws_client.receive_task
    if receive_task is None:
        reason = "no_receive_task"
        health_state.record_session_end(reason)
        _logger.warning("飞书 WS：无收包任务，结束会话监督")
        try:
            await ws_client._disconnect()
        except Exception as error:
            _logger.debug("无收包任务时断开连接失败: %s", error)
        return reason

    watchdog_task = asyncio.create_task(
        _watchdog_loop(
            ws_client,
            config,
            session_start,
            shutdown_event,
            exit_event,
            reason_holder,
            health_state,
        )
    )

    wait_shutdown = asyncio.create_task(shutdown_event.wait())
    wait_exit = asyncio.create_task(exit_event.wait())
    helper_tasks = {watchdog_task, wait_shutdown, wait_exit}

    try:
        done, pending = await asyncio.wait(
            {receive_task, wait_shutdown, wait_exit},
            return_when=asyncio.FIRST_COMPLETED,
        )
        await _cancel_and_consume(pending)

        # 显式检索 receive_task 异常（可能是正常关闭的
        # ConnectionClosedOK），避免 "Task exception was never retrieved"。
        if receive_task.done():
            try:
                receive_task.exception()
            except (asyncio.CancelledError, Exception) as e:
                _logger.debug("接收任务异常: %s", e)

        if shutdown_event.is_set():
            reason = "shutdown"
        elif exit_event.is_set():
            reason = reason_holder[0]
        elif receive_task in done:
            reason = _receive_loop_exit_reason(receive_task)
        else:
            reason = reason_holder[0]

        health_state.record_session_end(reason)
        _logger.info("飞书 WS 会话监督结束，原因=%s", reason)
        try:
            await ws_client._disconnect()
        except Exception as e:
            _logger.debug("supervise_feishu_ws_session disconnect: %s", e)
        return reason
    except asyncio.CancelledError:
        health_state.record_session_end("shutdown" if shutdown_event.is_set() else "cancelled")
        await _cancel_and_consume({receive_task})
        try:
            await ws_client._disconnect()
        except Exception as error:
            _logger.debug("取消监督时断开连接失败: %s", error)
        raise
    finally:
        await _cancel_and_consume(helper_tasks)


__all__ = [
    "FeishuWsHealthConfig",
    "FeishuWsHealthState",
    "read_feishu_ws_health_config",
    "supervise_feishu_ws_session",
]
