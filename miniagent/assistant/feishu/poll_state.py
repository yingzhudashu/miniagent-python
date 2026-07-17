"""飞书轮询连接拥有的状态与入站媒体解析。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, Protocol

from miniagent.agent.logging import get_logger
from miniagent.ui.feishu.types import FeishuConfig

_logger = get_logger(__name__)

class FeishuMediaHandler(Protocol):
    """file/image 入站异步处理：成功或已落盘应返回非「⚠️」前缀字符串；失败返回 ``⚠️`` 前缀以便不入磁盘去重。"""

    async def __call__(
        self,
        config: FeishuConfig,
        message_id: str,
        chat_id: str,
        sender_id: str,
        chat_type: str,
        msg_type: str,
        file_key: str,
        suggested_name: str,
        resource_type: str,
        thread_id: str | None = None,
    ) -> str | None: ...


def _parse_feishu_media_payload(msg_type: str, content_str: str) -> tuple[str, str, str] | None:
    """解析 file/image 消息的 file_key 与建议文件名。返回 (resource_type, file_key, suggested_name)。"""
    try:
        d = json.loads(content_str or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if msg_type == "file":
        fk = d.get("file_key")
        name = d.get("file_name") or d.get("name") or "download"
        if not fk:
            return None
        return ("file", str(fk), str(name))
    if msg_type == "image":
        ik = d.get("image_key")
        if not ik:
            return None
        return ("image", str(ik), "image")
    return None


def _extract_post_media_items(content_str: str) -> list[tuple[str, str, str]]:
    """从 post 富文本 JSON 中收集 (resource_type, file_key_or_image_key, suggested_name)。

    性能优化：迭代替代递归，限制遍历深度（防止恶意深层 JSON）。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    max_depth = 10  # 限制遍历深度

    try:
        root = json.loads(content_str or "{}")
    except (json.JSONDecodeError, TypeError):
        return []

    # 性能优化：迭代遍历替代递归
    stack: list[tuple[Any, int]] = [(root, 0)]  # (node, depth)
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            continue  # 超过深度限制，跳过
        if isinstance(node, dict):
            tag = node.get("tag")
            if tag == "img":
                ik = node.get("image_key") or node.get("image_token")
                if ik and ("image", str(ik)) not in seen:
                    seen.add(("image", str(ik)))
                    out.append(("image", str(ik), "image"))
            elif tag == "media":
                fk = node.get("file_key")
                if fk and ("file", str(fk)) not in seen:
                    seen.add(("file", str(fk)))
                    nm = node.get("file_name") or node.get("name") or "download"
                    out.append(("file", str(fk), str(nm)))
            # 将子节点加入栈（反向顺序保持深度优先顺序）
            for v in reversed(list(node.values())):
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            # 反向顺序保持原始遍历顺序
            for x in reversed(node):
                stack.append((x, depth + 1))

    return out


class FeishuPollState:
    """Connection state owned by one ``FeishuRuntime`` instance."""

    def __init__(self) -> None:
        from miniagent.assistant.feishu.cards.dedupe import CardActionDeduplicator
        from miniagent.assistant.feishu.feishu_dedup import FeishuDeduplicator
        from miniagent.assistant.feishu.message_debounce import FeishuMessageDebouncer
        from miniagent.assistant.feishu.ws_health import FeishuWsHealthState

        self.client: Any | None = None
        self.app_id: str | None = None
        self.shutdown_event: asyncio.Event | None = None
        self.debouncer = FeishuMessageDebouncer()
        self.deduplicator = FeishuDeduplicator()
        self.card_actions = CardActionDeduplicator()
        self.ws_health = FeishuWsHealthState()
        self.confirmation_engine: Any | None = None
        self.channel_router: Any | None = None
        self.callback_tasks: set[asyncio.Task[Any]] = set()

    def bind_confirmation(self, engine: Any, channel_router: Any | None) -> None:
        """Bind confirmation routing dependencies to this Feishu runtime."""
        self.confirmation_engine = engine
        self.channel_router = channel_router

    def request_shutdown(self) -> None:
        """Signal the active supervised session, if any, to stop."""
        if self.shutdown_event is not None:
            self.shutdown_event.set()

    def spawn_callback_task(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any]:
        """Track async work bridged from a synchronous SDK callback."""
        async def _run_awaitable() -> Any:
            return await awaitable

        try:
            task = asyncio.create_task(_run_awaitable())
        except RuntimeError:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise
        self.callback_tasks.add(task)

        def _done(completed: asyncio.Task[Any]) -> None:
            self.callback_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                _logger.error("飞书回调任务异常: %s", error, exc_info=error)

        task.add_done_callback(_done)
        return task

    async def reset(self) -> None:
        """Disconnect the active SDK client and clear pending debounce tasks."""
        callback_tasks = [task for task in self.callback_tasks if not task.done()]
        for task in callback_tasks:
            task.cancel()
        if callback_tasks:
            await asyncio.gather(*callback_tasks, return_exceptions=True)
        self.callback_tasks.clear()
        await self.debouncer.reset()
        await self.deduplicator.close()
        client = self.client
        self.client = None
        self.app_id = None
        self.shutdown_event = None
        if client is not None:
            try:
                await client._disconnect()
            except Exception as error:
                _logger.debug("FeishuPollState.reset: %s", error)


def _feishu_media_reply_indicates_failure(reply: str | None) -> bool:
    """media_handler 用「⚠️」前缀表示不可落盘的失败类回复。"""
    if not reply:
        return False
    return reply.lstrip().startswith("\u26a0\ufe0f")


# ─── 长轮询入口：WSClient、事件回调、handler 内投递 message_queue ───
# 与 ``# ─── 消息队列 ───`` 注释呼应：此处只负责连接与解析，顺序语义由传入的 ``message_queue`` 保证。


__all__ = [
    "FeishuMediaHandler",
    "FeishuPollState",
    "_extract_post_media_items",
    "_feishu_media_reply_indicates_failure",
    "_parse_feishu_media_payload",
]
