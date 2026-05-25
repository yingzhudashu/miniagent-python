"""Confirmation side-channel — 基于 asyncio.Event 的暂停/恢复机制。

与消息队列独立：agent 执行线程调用 ``request_confirmation()`` 暂停，
用户通过 CLI 点命令或飞书按钮回调调用 ``respond()`` 恢复。
这确保确认交互不经过消息队列，也不会被当作普通消息处理。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miniagent.types.confirmation import ConfirmationRequest, ConfirmationResult

_logger = logging.getLogger(__name__)


class ConfirmationChannel:
    """确认侧通道。

    每个需要独立确认的场景（如不同 chat_id 的会话）应使用独立实例。
    """

    def __init__(self) -> None:
        self._pending: ConfirmationRequest | None = None
        self._event = asyncio.Event()
        self._event.set()  # 初始为已设置状态（无待确认请求）
        self._result: ConfirmationResult | None = None

    async def request_confirmation(self, req: ConfirmationRequest) -> ConfirmationResult:
        """发送确认请求并等待用户响应。

        调用方会在此阻塞，直到用户通过 ``respond()`` 提供响应。

        Args:
            req: 确认请求

        Returns:
            用户的确认结果
        """
        _logger.info(
            "request_confirmation(): 设置待确认请求 stage=%s",
            getattr(req.stage, "value", req.stage),
        )
        self._pending = req
        self._result = None
        self._event.clear()
        await self._event.wait()
        _logger.info("request_confirmation(): 已收到响应，恢复执行")
        result = self._result
        self._pending = None
        return result  # type: ignore[return-value]

    def respond(self, result: ConfirmationResult) -> None:
        """提交确认响应，恢复被暂停的 agent 线程。

        Args:
            result: 用户的确认结果
        """
        if self._pending is None:
            _logger.debug("respond(): 无待确认请求，跳过")
            return
        _logger.info(
            "respond(): 设置确认结果 approved=%s, adjustment=%s",
            result.approved,
            (result.adjustment or "")[:60] if hasattr(result, "adjustment") else "N/A",
        )
        self._result = result
        self._event.set()

    @property
    def pending(self) -> ConfirmationRequest | None:
        """当前待确认的请求，无则为 None。"""
        return self._pending

    @property
    def has_pending(self) -> bool:
        """是否有待确认的请求。"""
        return self._pending is not None


__all__ = ["ConfirmationChannel"]
