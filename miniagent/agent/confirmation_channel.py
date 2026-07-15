"""Confirmation side-channel — 基于 asyncio.Event 的暂停/恢复机制。

与消息队列独立：agent 执行线程调用 ``request_confirmation()`` 暂停，
用户通过 CLI 点命令或飞书按钮回调调用 ``respond()`` 恢复。
这确保确认交互不经过消息队列，也不会被当作普通消息处理。
"""

from __future__ import annotations

import asyncio
import logging
import threading

from miniagent.agent.types.confirmation import ConfirmationRequest, ConfirmationResult

_logger = logging.getLogger(__name__)


class ConfirmationChannel:
    """确认侧通道。

    每个需要独立确认的场景（如不同 chat_id 的会话）应使用独立实例。

    **并发保护**：槽位在 ``_pending`` 非空时即被占用（含已响应、尚未被
    ``request_confirmation()`` 消费完毕的短暂窗口），禁止并发 ``request_confirmation()``。

    **重复响应**：``respond()`` 在已响应或槽位空闲时静默忽略，防止连点覆盖结果。

    **线程安全**：``respond()`` 可从同步回调线程调用；共享状态由 ``threading.Lock`` 保护。
    """

    def __init__(self) -> None:
        self._pending: ConfirmationRequest | None = None
        self._event = asyncio.Event()
        self._event.set()  # 初始为已设置状态（无待确认请求）
        self._result: ConfirmationResult | None = None
        self._lock = threading.Lock()

    def _is_waiting_for_user(self) -> bool:
        return self._pending is not None and not self._event.is_set()

    async def request_confirmation(self, req: ConfirmationRequest) -> ConfirmationResult:
        """发送确认请求并等待用户响应。

        调用方会在此阻塞，直到用户通过 ``respond()`` 提供响应。

        Args:
            req: 确认请求

        Returns:
            用户的确认结果

        Raises:
            RuntimeError: 槽位已被占用（等待中或响应尚未消费完毕）
        """
        with self._lock:
            if self._pending is not None:
                raise RuntimeError("已有确认请求正在处理，无法并发处理多个请求")

            _logger.info(
                "request_confirmation(): 设置待确认请求 stage=%s",
                getattr(req.stage, "value", req.stage),
            )
            self._pending = req
            self._result = None
            self._event.clear()

        # 等待响应（锁已释放，允许 respond() 执行）
        await self._event.wait()

        with self._lock:
            _logger.info("request_confirmation(): 已收到响应，恢复执行")
            result = self._result
            self._pending = None
            self._result = None
            if result is None:
                raise RuntimeError("确认响应为 None，respond() 可能未被正确调用")
            return result

    def respond(self, result: ConfirmationResult) -> None:
        """提交确认响应，恢复被暂停的 agent 协程。

        若当前无待确认请求、或用户已响应（重复点击），则静默忽略。

        可从非 asyncio 线程调用（如飞书卡片回调）；内部使用 ``threading.Lock``。

        Args:
            result: 用户的确认结果
        """
        with self._lock:
            if not self._is_waiting_for_user():
                _logger.debug(
                    "respond(): 无待确认请求或已响应，跳过 (pending=%s, event_set=%s)",
                    self._pending is not None,
                    self._event.is_set(),
                )
                return
            _logger.info(
                "respond(): 设置确认结果 approved=%s, rejected=%s, adjustment=%s",
                result.approved,
                result.rejected,
                (result.adjustment or "")[:60],
            )
            self._result = result
            self._event.set()

    @property
    def pending(self) -> ConfirmationRequest | None:
        """当前等待用户输入的确认请求；已响应或空闲时为 None。"""
        with self._lock:
            if self._is_waiting_for_user():
                return self._pending
            return None

    @property
    def has_pending(self) -> bool:
        """是否正在等待用户响应（不含已响应、尚未消费的短暂窗口）。"""
        with self._lock:
            return self._is_waiting_for_user()


__all__ = ["ConfirmationChannel"]
