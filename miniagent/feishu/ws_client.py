"""飞书 WebSocket 客户端薄封装：追踪收包循环任务，默认由应用层负责重连。

``_connect`` 与 ``lark_oapi.ws.client.Client`` 对齐；升级 ``lark-oapi`` 时请对照 SDK 的 ``_connect`` 实现。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets

# lark-oapi 是可选依赖；未安装时提供 placeholder，避免 import 阻塞测试
try:
    from lark_oapi.core.log import logger as _lark_logger
    from lark_oapi.ws.client import Client as _LarkWsClient
    from lark_oapi.ws.client import _parse_ws_conn_exception
    from lark_oapi.ws.const import DEVICE_ID, SERVICE_ID

    _HAS_LARK_OAPI = True
except ImportError:
    _LarkWsClient = object  # Placeholder base class
    _lark_logger = logging.getLogger(__name__)  # Fallback logger
    _HAS_LARK_OAPI = False

    # Placeholder constants (not used when lark-oapi is missing)
    DEVICE_ID = "device_id"
    SERVICE_ID = "service_id"

    def _parse_ws_conn_exception(exc: Exception) -> None:  # noqa: ARG001
        """Placeholder for parsing WS connection exceptions."""
        raise exc


# Use lark-oapi logger if available, otherwise use fallback
logger = _lark_logger


def feishu_ws_auto_reconnect_enabled() -> bool:
    """是否启用 lark-oapi SDK 内建 ``auto_reconnect``（默认关闭，由应用外层退避重连）。"""
    raw = (os.environ.get("MINIAGENT_FEISHU_WS_AUTO_RECONNECT") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


class FeishuWsClient(_LarkWsClient):
    """继承 ``lark.ws.Client``，在 ``_connect`` 时保存 ``_receive_message_loop`` 任务句柄。"""

    def __init__(
        self,
        *args: Any,
        auto_reconnect: bool | None = None,
        **kwargs: Any,
    ) -> None:
        if auto_reconnect is None:
            auto_reconnect = feishu_ws_auto_reconnect_enabled()
        super().__init__(*args, auto_reconnect=auto_reconnect, **kwargs)
        self._receive_task: asyncio.Task[Any] | None = None

    async def _connect(self) -> None:
        await self._lock.acquire()
        if self._conn is not None:
            self._lock.release()
            return
        try:
            conn_url = self._get_conn_url()
            u = urlparse(conn_url)
            q = parse_qs(u.query)
            conn_id = q[DEVICE_ID][0]
            service_id = q[SERVICE_ID][0]

            conn = await websockets.connect(conn_url)
            self._conn = conn
            self._conn_url = conn_url
            self._conn_id = conn_id
            self._service_id = service_id

            logger.info(self._fmt_log("connected to {}", conn_url))
            self._receive_task = asyncio.get_running_loop().create_task(
                self._receive_message_loop()
            )
        except websockets.InvalidStatusCode as e:
            _parse_ws_conn_exception(e)
        finally:
            self._lock.release()

    @property
    def connected(self) -> bool:
        return self._conn is not None

    @property
    def receive_task(self) -> asyncio.Task[Any] | None:
        return self._receive_task

    @property
    def conn_id(self) -> str:
        return self._conn_id


__all__ = ["FeishuWsClient", "feishu_ws_auto_reconnect_enabled"]
