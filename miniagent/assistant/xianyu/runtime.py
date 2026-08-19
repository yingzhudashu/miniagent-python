"""Lifecycle-owned Xianyu WebSocket connection and RPC coordination."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from miniagent.agent.lifecycle import HealthReport, HealthState
from miniagent.assistant.infrastructure.inbound_dedup import InboundDeduplicator
from miniagent.assistant.xianyu.client import XianyuClient
from miniagent.assistant.xianyu.errors import (
    XianyuAuthenticationError,
    XianyuDependencyError,
    XianyuProtocolError,
)
from miniagent.assistant.xianyu.protocol import (
    XianyuInbound,
    build_ack,
    build_create_chat,
    build_history_request,
    build_registration,
    build_send_message,
    build_sync_ack,
    parse_cookie_header,
    parse_inbound_frame,
)

_logger = logging.getLogger(__name__)
_WS_URL = "wss://wss-goofish.dingtalk.com/"
_HEARTBEAT_SECONDS = 15.0
_REFRESH_SECONDS = 600.0
_RPC_TIMEOUT_SECONDS = 20.0
InboundHandler = Callable[[XianyuInbound], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class XianyuStatus:
    """Snapshot of Xianyu connection, authentication, pause, and error state."""
    enabled: bool
    connected: bool
    authenticated: bool
    paused: bool
    owner_id: str
    last_error: str
    reconnect_attempt: int


class XianyuRuntime:
    """Own exactly one Xianyu account, client, socket and reconnect loop."""

    def __init__(self) -> None:
        self.client: XianyuClient | None = None
        self._socket: Any | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._handler: InboundHandler | None = None
        self._enabled = False
        self._connected = False
        self._authenticated = False
        self._paused = False
        self._paused_conversations: set[str] = set()
        self._last_error = ""
        self._reconnect_attempt = 0
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._send_lock = asyncio.Lock()
        self._deduplicator = InboundDeduplicator("xianyu")

    async def configure(self, cookie: str, handler: InboundHandler) -> None:
        """Configure the single-account client and inbound buyer handler."""
        cookies = parse_cookie_header(cookie)
        if not cookies:
            raise XianyuAuthenticationError("secrets.xianyu_cookie is empty")
        previous = self.client
        self.client = XianyuClient(cookies)
        self._handler = handler
        if previous is not None:
            await previous.close()

    async def start(self) -> None:
        """Start the reconnecting WebSocket runtime task."""
        if self._runner and not self._runner.done():
            return
        if self.client is None or self._handler is None:
            raise XianyuAuthenticationError("Xianyu runtime is not configured")
        self._enabled = True
        self._stop = asyncio.Event()
        self._runner = asyncio.create_task(self._run(), name="xianyu-runtime")
        await asyncio.sleep(0)

    async def stop(self) -> None:
        """Cancel runtime tasks and close socket, HTTP client, and dedup state."""
        self._enabled = False
        self._stop.set()
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        await self._close_socket()
        if self.client is not None:
            await self.client.close()
            self.client = None
        await self._deduplicator.close()
        self._connected = False
        self._authenticated = False
        self._fail_pending(RuntimeError("Xianyu runtime stopped"))

    async def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                delay = 1.0
                self._reconnect_attempt = 0
            except asyncio.CancelledError:
                raise
            except XianyuAuthenticationError as error:
                self._last_error = str(error)
                self._authenticated = False
                self._enabled = False
                _logger.error("Xianyu authentication stopped: %s", error)
                return
            except Exception as error:
                self._last_error = str(error)
                self._connected = False
                self._reconnect_attempt += 1
                _logger.warning("Xianyu connection failed; retrying in %.0fs: %s", delay, error)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(delay * 2, 60.0)

    async def _connect_once(self) -> None:
        assert self.client is not None
        try:
            import websockets
        except ImportError as error:
            raise XianyuDependencyError(
                "Xianyu WebSocket requires 'websockets'; install miniagent-python[xianyu]"
            ) from error
        access_token = await self.client.get_access_token()
        headers = {
            "Cookie": self.client.cookie_header(),
            "Origin": "https://www.goofish.com",
            "User-Agent": "Mozilla/5.0",
        }
        async with websockets.connect(
            _WS_URL,
            extra_headers=headers,
            ping_interval=None,
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        ) as socket:
            self._socket = socket
            self._connected = True
            self._authenticated = True
            self._last_error = ""
            await self._send_json(build_registration(access_token, self.client.device_id))
            await self._send_json(build_sync_ack())
            heartbeat = asyncio.create_task(self._heartbeat(), name="xianyu-heartbeat")
            refresh = asyncio.create_task(self._refresh_loop(), name="xianyu-refresh")
            try:
                async for raw in socket:
                    await self._handle_raw(raw)
            finally:
                heartbeat.cancel()
                refresh.cancel()
                await asyncio.gather(heartbeat, refresh, return_exceptions=True)
                self._socket = None
                self._connected = False
                self._fail_pending(ConnectionError("Xianyu socket disconnected"))
        if not self._stop.is_set():
            raise ConnectionError("Xianyu socket closed")

    async def _handle_raw(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            frame = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise XianyuProtocolError("Xianyu WebSocket returned invalid JSON") from error
        if not isinstance(frame, dict):
            raise XianyuProtocolError("Xianyu WebSocket frame must be an object")
        await self._send_json(build_ack(frame))
        headers = (
            cast(dict[str, Any], frame.get("headers"))
            if isinstance(frame.get("headers"), dict)
            else {}
        )
        mid = str(headers.get("mid") or "")
        pending = self._pending.pop(mid, None)
        if pending is not None and not pending.done():
            pending.set_result(frame)
            return
        if frame.get("lwp") == "/s/vulcan":
            return
        inbound = parse_inbound_frame(frame)
        if inbound is None or inbound.sender_id == (self.client.owner_id if self.client else ""):
            return
        if self._paused or inbound.conversation_id in self._paused_conversations:
            return
        if inbound.occurred_at_ms and inbound.occurred_at_ms < int((time.time() - 300) * 1000):
            return
        claimed = await asyncio.to_thread(
            self._deduplicator.try_begin_processing, inbound.message_id
        )
        if not claimed:
            return
        assert self._handler is not None
        try:
            reply = await self._handler(inbound)
        except Exception:
            await asyncio.to_thread(self._deduplicator.abandon, inbound.message_id)
            _logger.exception("Xianyu buyer turn failed")
            return
        if not reply or not reply.strip():
            await asyncio.to_thread(self._deduplicator.abandon, inbound.message_id)
            return
        try:
            await self.send_text(inbound.conversation_id, inbound.sender_id, reply.strip())
        except Exception:
            await asyncio.to_thread(self._deduplicator.abandon, inbound.message_id)
            raise
        await asyncio.to_thread(self._deduplicator.complete, inbound.message_id)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            await self._send_json({"lwp": "/!", "headers": {"mid": str(int(time.time() * 1000))}})

    async def _refresh_loop(self) -> None:
        assert self.client is not None
        while True:
            await asyncio.sleep(_REFRESH_SECONDS)
            await self.client.refresh_login()

    async def _send_json(self, frame: dict[str, Any]) -> None:
        if self._socket is None:
            raise ConnectionError("Xianyu WebSocket is not connected")
        async with self._send_lock:
            await self._socket.send(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))

    async def _request(self, frame: dict[str, Any]) -> dict[str, Any]:
        mid = str((frame.get("headers") or {}).get("mid") or "")
        if not mid:
            raise ValueError("Xianyu RPC frame is missing mid")
        future = asyncio.get_running_loop().create_future()
        self._pending[mid] = future
        try:
            await self._send_json(frame)
            return await asyncio.wait_for(future, timeout=_RPC_TIMEOUT_SECONDS)
        finally:
            self._pending.pop(mid, None)

    async def send_text(self, conversation_id: str, receiver_id: str, text: str) -> None:
        """Send text to an existing buyer conversation."""
        if not text.strip():
            raise ValueError("text must not be empty")
        if self.client is None:
            raise RuntimeError("Xianyu runtime is not configured")
        await self._send_json(
            build_send_message(
                owner_id=self.client.owner_id,
                conversation_id=conversation_id,
                receiver_id=receiver_id,
                kind="text",
                value={"text": text},
            )
        )

    async def send_image(
        self, conversation_id: str, receiver_id: str, *, url: str, width: int, height: int
    ) -> None:
        """Send an already-uploaded image to an existing conversation."""
        if self.client is None:
            raise RuntimeError("Xianyu runtime is not configured")
        await self._send_json(
            build_send_message(
                owner_id=self.client.owner_id,
                conversation_id=conversation_id,
                receiver_id=receiver_id,
                kind="image",
                value={"url": url, "width": width, "height": height},
            )
        )

    async def create_chat(self, receiver_id: str, item_id: str) -> str:
        """Create a buyer conversation and return its conversation identifier."""
        if self.client is None:
            raise RuntimeError("Xianyu runtime is not configured")
        response = await self._request(
            build_create_chat(self.client.owner_id, receiver_id, item_id)
        )
        def find_conversation(value: Any) -> str:
            if isinstance(value, dict):
                for key in ("cid", "conversationId", "conversation_id", "sid"):
                    candidate = str(value.get(key) or "")
                    if candidate:
                        return candidate
                for child in value.values():
                    candidate = find_conversation(child)
                    if candidate:
                        return candidate
            elif isinstance(value, list):
                for child in value:
                    candidate = find_conversation(child)
                    if candidate:
                        return candidate
            return ""

        conversation_id = find_conversation(response.get("body")).split("@", 1)[0]
        if not conversation_id:
            raise XianyuProtocolError("Xianyu create-chat response returned no conversation id")
        return conversation_id

    async def get_history(self, conversation_id: str) -> list[dict[str, Any]]:
        """Fetch and normalize all pages of one conversation's history."""
        cursor = 9007199254740991
        result: list[dict[str, Any]] = []
        while True:
            _mid, request = build_history_request(conversation_id, cursor)
            response = await self._request(request)
            body = response.get("body") or {}
            if not isinstance(body, dict):
                raise XianyuProtocolError("Xianyu history response body is invalid")
            for model in body.get("userMessageModels") or []:
                message = model.get("message") if isinstance(model, dict) else None
                if not isinstance(message, dict):
                    continue
                extension = message.get("extension") or {}
                custom = ((message.get("content") or {}).get("custom") or {}).get("data")
                decoded = {}
                if custom:
                    from miniagent.assistant.xianyu.protocol import decode_custom_data

                    decoded = decode_custom_data(str(custom))
                result.insert(
                    0,
                    {
                        "sender_id": str(extension.get("senderUserId") or ""),
                        "sender_name": str(extension.get("reminderTitle") or ""),
                        "message_id": str(extension.get("messageId") or message.get("uuid") or ""),
                        "content": decoded,
                        "created_at": message.get("createTime") or message.get("timestamp"),
                    },
                )
            if int(body.get("hasMore") or 0) != 1:
                return result
            cursor = int(body.get("nextCursor") or 0)
            if cursor <= 0:
                raise XianyuProtocolError("Xianyu history pagination omitted nextCursor")

    def pause(self, conversation_id: str | None = None) -> None:
        """Pause inbound replies globally or for one conversation."""
        if conversation_id:
            self._paused_conversations.add(conversation_id)
        else:
            self._paused = True

    def resume(self, conversation_id: str | None = None) -> None:
        """Resume inbound replies globally or for one conversation."""
        if conversation_id:
            self._paused_conversations.discard(conversation_id)
        else:
            self._paused = False

    def status(self) -> XianyuStatus:
        """Return the current runtime status snapshot."""
        return XianyuStatus(
            enabled=self._enabled,
            connected=self._connected,
            authenticated=self._authenticated,
            paused=self._paused,
            owner_id=self.client.owner_id if self.client else "",
            last_error=self._last_error,
            reconnect_attempt=self._reconnect_attempt,
        )

    def health(self) -> HealthReport:
        """Return a health report suitable for lifecycle monitoring."""
        status = self.status()
        if status.connected:
            return HealthReport(HealthState.READY)
        if status.enabled:
            return HealthReport(HealthState.DEGRADED, status.last_error or "connecting")
        return HealthReport(HealthState.STOPPED, status.last_error or "disabled")

    async def _close_socket(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()

    def _fail_pending(self, error: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)


_runtime: XianyuRuntime | None = None


def install_xianyu_runtime(runtime: XianyuRuntime) -> None:
    """Install the process-wide Xianyu runtime used by command handlers."""
    global _runtime
    _runtime = runtime


def get_xianyu_runtime() -> XianyuRuntime:
    """Return the installed process-wide Xianyu runtime."""
    if _runtime is None:
        raise RuntimeError("Xianyu runtime is not installed")
    return _runtime


__all__ = [
    "InboundHandler",
    "XianyuRuntime",
    "XianyuStatus",
    "get_xianyu_runtime",
    "install_xianyu_runtime",
]
