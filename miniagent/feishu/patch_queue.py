"""飞书思考卡片 PATCH 队列管理器。

解决流式 PATCH 请求堆积问题，进一步提升流式输出的丝滑体验：
- 队列缓冲 PATCH 请求
- 独立任务处理队列
- 防止飞书 API 限流
- 可选开启/关闭

使用方法：
    from miniagent.feishu.patch_queue import PatchQueueManager

    manager = PatchQueueManager(max_queue_size=10)
    ok = await manager.enqueue(config, message_id, card_json)

配置项（config.defaults.json）：
    feishu.patch.queue_enabled: true  # 是否启用队列
    feishu.patch.queue_max_size: 10   # 队列最大长度
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


class PatchQueueManager:
    """飞书 PATCH 队列管理器。

    队列化处理 PATCH 请求，避免请求堆积阻塞 LLM 流式处理。
    """

    def __init__(self, max_queue_size: int = 10):
        """初始化队列管理器。

        Args:
            max_queue_size: 队列最大长度，超出时丢弃最早的请求
        """
        self._queue: deque[tuple[str, str, asyncio.Future]] = deque(maxlen=max_queue_size)
        self._processing = False
        self._task: asyncio.Task | None = None
        self._config: Any = None
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        config: Any,
        message_id: str,
        card_json: str,
    ) -> bool:
        """将 PATCH 请求加入队列并等待结果。

        Args:
            config: 飞书配置
            message_id: 要更新的消息 ID
            card_json: 新卡片内容 JSON

        Returns:
            bool: PATCH 成功返回 True
        """
        future: asyncio.Future[bool] = asyncio.Future()

        async with self._lock:
            self._config = config
            self._queue.append((message_id, card_json, future))

            if not self._processing:
                self._task = asyncio.create_task(self._process_queue())

        return await future

    async def _process_queue(self) -> None:
        """处理队列中的 PATCH 请求。

        独立任务循环，防止阻塞事件循环。
        """
        from miniagent.feishu.poll_server import _patch_interactive_thinking_message_async

        self._processing = True
        try:
            while self._queue:
                async with self._lock:
                    if not self._queue:
                        break
                    mid, card, future = self._queue.popleft()

                try:
                    ok = await _patch_interactive_thinking_message_async(
                        self._config, mid, card, timeout=10.0
                    )
                    if not future.done():
                        future.set_result(ok)
                except Exception as e:
                    _logger.debug("队列 PATCH 处理异常: %s", e)
                    if not future.done():
                        future.set_result(False)

                # 短暂间隔，避免飞书 API 限流
                await asyncio.sleep(0.05)
        finally:
            self._processing = False

    async def clear(self) -> None:
        """清空队列。"""
        async with self._lock:
            while self._queue:
                _, _, future = self._queue.popleft()
                if not future.done():
                    future.set_result(False)

    @property
    def size(self) -> int:
        """当前队列长度。"""
        return len(self._queue)


# 全局队列管理器（可选使用）
_global_manager: PatchQueueManager | None = None


def get_patch_queue_manager() -> PatchQueueManager | None:
    """获取全局 PATCH 队列管理器（如果启用）。

    Returns:
        PatchQueueManager | None: 启用时返回管理器，否则返回 None
    """
    global _global_manager

    from miniagent.core.constants import FEISHU_PATCH_QUEUE_ENABLED, FEISHU_PATCH_QUEUE_MAX_SIZE

    if not FEISHU_PATCH_QUEUE_ENABLED:
        return None

    if _global_manager is None:
        max_size = FEISHU_PATCH_QUEUE_MAX_SIZE
        _global_manager = PatchQueueManager(max_queue_size=max_size)

    return _global_manager


def reset_patch_queue_manager() -> None:
    """重置全局队列管理器（测试用）。

    注意：clear() 是异步方法，这里只是标记管理器为 None，
    队列中的请求会在下次循环中自然清理。
    """
    global _global_manager
    # 不调用 clear()，直接重置
    _global_manager = None


__all__ = [
    "PatchQueueManager",
    "get_patch_queue_manager",
    "reset_patch_queue_manager",
]