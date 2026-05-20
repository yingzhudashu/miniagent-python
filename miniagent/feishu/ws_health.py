"""飞书 WebSocket 会话健康监督：看门狗、定期刷新、结束原因可观测。"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

from miniagent.feishu.ws_client import FeishuWsClient, feishu_ws_auto_reconnect_enabled
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# 最近一次入站（monotonic）与最近一次会话结束信息（供 status / 诊断）
_ws_last_inbound_monotonic: float | None = None
_last_session_end_reason: str | None = None
_last_session_end_at: float | None = None  # time.time()


def touch_ws_inbound_activity() -> None:
    """入站回调入口调用，更新最后活动时间。"""
    global _ws_last_inbound_monotonic
    _ws_last_inbound_monotonic = time.monotonic()


def get_ws_last_inbound_monotonic() -> float | None:
    return _ws_last_inbound_monotonic


def get_last_ws_session_end() -> tuple[str | None, float | None]:
    """返回 ``(reason, ended_at_unix)``。"""
    return _last_session_end_reason, _last_session_end_at


def _record_session_end(reason: str) -> None:
    global _last_session_end_reason, _last_session_end_at
    _last_session_end_reason = reason
    _last_session_end_at = time.time()


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class FeishuWsHealthConfig:
    watchdog_interval_s: float
    dead_conn_grace_s: float
    reconnect_grace_s: float
    refresh_interval_s: float
    idle_refresh_s: float


def read_feishu_ws_health_config() -> FeishuWsHealthConfig:
    return FeishuWsHealthConfig(
        watchdog_interval_s=_env_float("MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S", 30.0),
        dead_conn_grace_s=_env_float("MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S", 90.0),
        reconnect_grace_s=_env_float("MINIAGENT_FEISHU_WS_RECONNECT_GRACE_S", 300.0),
        refresh_interval_s=_env_float("MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S", 0.0),
        idle_refresh_s=_env_float("MINIAGENT_FEISHU_WS_IDLE_REFRESH_S", 0.0),
    )


def _receive_loop_exit_reason(task: asyncio.Task[Any]) -> str:
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
) -> None:
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
            pass

        if shutdown_event.is_set() or exit_event.is_set():
            return

        now = time.monotonic()

        if config.refresh_interval_s > 0 and (now - session_start) >= config.refresh_interval_s:
            reason_holder[0] = "watchdog_refresh"
            exit_event.set()
            return

        if config.idle_refresh_s > 0 and _ws_last_inbound_monotonic is not None:
            if (now - _ws_last_inbound_monotonic) >= config.idle_refresh_s:
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
) -> str:
    """监督 WebSocket 会话直至应结束；返回结束原因字符串。"""
    config = read_feishu_ws_health_config()
    session_start = time.monotonic()
    exit_event = asyncio.Event()
    reason_holder: list[str] = ["unknown"]

    receive_task = ws_client.receive_task
    if receive_task is None:
        reason = "no_receive_task"
        _record_session_end(reason)
        _logger.warning("飞书 WS：无收包任务，结束会话监督")
        return reason

    watchdog_task = asyncio.create_task(
        _watchdog_loop(
            ws_client,
            config,
            session_start,
            shutdown_event,
            exit_event,
            reason_holder,
        )
    )

    wait_shutdown = asyncio.create_task(shutdown_event.wait())
    wait_exit = asyncio.create_task(exit_event.wait())

    try:
        done, pending = await asyncio.wait(
            {receive_task, wait_shutdown, wait_exit},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        if shutdown_event.is_set():
            reason = "shutdown"
        elif exit_event.is_set():
            reason = reason_holder[0]
        elif receive_task in done:
            reason = _receive_loop_exit_reason(receive_task)
        else:
            reason = reason_holder[0]

        _record_session_end(reason)
        _logger.info("飞书 WS 会话监督结束，原因=%s", reason)
        try:
            await ws_client._disconnect()
        except Exception as e:
            _logger.debug("supervise_feishu_ws_session disconnect: %s", e)
        return reason
    except asyncio.CancelledError:
        _record_session_end("shutdown" if shutdown_event.is_set() else "cancelled")
        raise
    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


__all__ = [
    "FeishuWsHealthConfig",
    "get_last_ws_session_end",
    "get_ws_last_inbound_monotonic",
    "read_feishu_ws_health_config",
    "supervise_feishu_ws_session",
    "touch_ws_inbound_activity",
]
