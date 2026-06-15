"""飞书入站文本消息防抖：合并同一发送者短时内的连续消息。

同一 ``chat_id + sender_id + thread_id`` 窗口内的多条 text/interactive 入站，
在 ``feishu.message_debounce_ms`` 内合并为一条 ``FeishuInboundText`` 再派发。
命令（``/`` 开头）与澄清拦截路径不经过本模块。

配置：
- ``feishu.message_debounce_ms``：防抖窗口（毫秒）；``0`` 表示关闭。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from miniagent.feishu.types import FeishuInboundText
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

OnFlushCallback = Callable[[FeishuInboundText, list[str]], Awaitable[None]]


def feishu_message_debounce_ms() -> int:
    """读取防抖窗口（毫秒）；``0`` 表示关闭。"""
    raw = get_config("feishu.message_debounce_ms", 800)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 800


@dataclass
class _DebounceBuffer:
    """同一防抖键下待合并的入站片段。"""

    parts: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    latest: FeishuInboundText | None = None


class FeishuMessageDebouncer:
    """按会话键缓冲入站文本，窗口结束后合并并回调 ``on_flush``。"""

    def __init__(self) -> None:
        self._buffers: dict[str, _DebounceBuffer] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def debounce_key(inbound: FeishuInboundText) -> str:
        """防抖键：同聊天室、同发送者、同话题线程。"""
        thread = (inbound.thread_id or "").strip()
        return f"{inbound.chat_id}:{inbound.sender_id}:{thread}"

    async def schedule(
        self,
        inbound: FeishuInboundText,
        *,
        debounce_ms: int,
        on_flush: OnFlushCallback,
    ) -> None:
        """将一条入站文本加入缓冲；窗口到期后合并并调用 ``on_flush(merged, message_ids)``。"""
        if debounce_ms <= 0:
            await on_flush(inbound, [inbound.message_id])
            return

        key = self.debounce_key(inbound)
        async with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = _DebounceBuffer()
                self._buffers[key] = buf
            buf.parts.append(inbound.text)
            buf.message_ids.append(inbound.message_id)
            buf.latest = inbound

            old = self._tasks.pop(key, None)
            if old is not None and not old.done():
                old.cancel()

            async def _flush() -> None:
                try:
                    await asyncio.sleep(debounce_ms / 1000.0)
                except asyncio.CancelledError:
                    return
                async with self._lock:
                    pending = self._buffers.pop(key, None)
                    self._tasks.pop(key, None)
                if pending is None or pending.latest is None:
                    return
                merged_text = "\n".join(p for p in pending.parts if (p or "").strip())
                if not merged_text.strip():
                    return
                latest = pending.latest
                merged = FeishuInboundText(
                    text=merged_text,
                    chat_id=latest.chat_id,
                    sender_id=latest.sender_id,
                    chat_type=latest.chat_type,
                    message_id=latest.message_id,
                    root_id=latest.root_id,
                    parent_id=latest.parent_id,
                    thread_id=latest.thread_id,
                    create_time=latest.create_time,
                )
                if len(pending.message_ids) > 1:
                    _logger.debug(
                        "飞书防抖合并: key=%s, count=%d, chars=%d",
                        key[:24],
                        len(pending.message_ids),
                        len(merged_text),
                    )
                await on_flush(merged, list(pending.message_ids))

            self._tasks[key] = asyncio.create_task(_flush())

    async def reset(self) -> None:
        """取消全部待 flush 任务并清空缓冲（WS 关停/重连前调用）。"""
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
            self._buffers.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_message_debouncer = FeishuMessageDebouncer()


def get_feishu_message_debouncer() -> FeishuMessageDebouncer:
    """返回进程内单例防抖器。"""
    return _message_debouncer


async def reset_feishu_message_debouncer() -> None:
    """重置防抖器（测试与 WS 重连用）。"""
    await _message_debouncer.reset()


__all__ = [
    "FeishuMessageDebouncer",
    "feishu_message_debounce_ms",
    "get_feishu_message_debouncer",
    "reset_feishu_message_debouncer",
]
