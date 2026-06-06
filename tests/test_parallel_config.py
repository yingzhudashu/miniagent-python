"""parallel_config 与 MessageQueue 并行模式配置测试。"""

from __future__ import annotations

from unittest.mock import patch

from miniagent.engine.parallel_config import configure_message_queue_for_parallel
from miniagent.infrastructure.message_queue import MessageQueueManager


def test_configure_parallel_disables_cross_queue_serial() -> None:
    mq = MessageQueueManager()
    with patch(
        "miniagent.engine.parallel_config.get_config",
        side_effect=lambda key, default=None: True if key == "agent.parallel_sessions" else default,
    ):
        configure_message_queue_for_parallel(mq)
    assert mq.cross_queue_serial is False
    assert mq.exec_lock is None


def test_configure_serial_enables_exec_lock() -> None:
    mq = MessageQueueManager()
    with patch(
        "miniagent.engine.parallel_config.get_config",
        side_effect=lambda key, default=None: False if key == "agent.parallel_sessions" else default,
    ):
        configure_message_queue_for_parallel(mq)
    assert mq.cross_queue_serial is True
    assert mq.exec_lock is not None
