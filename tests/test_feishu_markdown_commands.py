"""MINIAGENT_FEISHU_MARKDOWN_COMMANDS 下的表格输出。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from miniagent.engine.cli_commands import cmd_queue_status, cmd_session_list
from miniagent.infrastructure.message_queue import MessageQueueManager


def test_cmd_session_list_markdown_table() -> None:
    class _SM:
        def list_all_sessions_with_info(self):
            return [
                {
                    "id": "s1",
                    "number": 1,
                    "title": "Alpha|Beta",
                    "turn_count": 2,
                    "locked": True,
                    "lock_pid": 99999,
                },
            ]

    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_session_list(_SM(), "s1", markdown=True)
    out = buf.getvalue()
    assert "| 编号 | 会话 | 轮次 | 备注 |" in out
    assert r"\|Beta" in out


def test_cmd_queue_status_markdown_table() -> None:
    mq = MessageQueueManager()
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_queue_status(mq, markdown=True)
    out = buf.getvalue()
    assert "## 消息队列状态" in out
    assert "| 会话 | 状态 | 等待条数 |" in out
