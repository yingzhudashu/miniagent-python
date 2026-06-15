"""``feishu.markdown_commands`` 配置与 Markdown 表格输出。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from miniagent.engine.cli_commands import cmd_queue_status, cmd_session_list
from miniagent.engine.commands.config_commands import feishu_markdown_commands_enabled
from miniagent.infrastructure.message_queue import MessageQueueManager
from tests.config_helpers import install_test_config


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
    ],
)
def test_feishu_markdown_commands_enabled(
    tmp_path, value: bool, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_MARKDOWN_COMMANDS", raising=False)
    install_test_config(tmp_path, {"feishu": {"markdown_commands": value}})
    assert feishu_markdown_commands_enabled() is expected


def test_feishu_markdown_commands_default_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_MARKDOWN_COMMANDS", raising=False)
    install_test_config(tmp_path)
    assert feishu_markdown_commands_enabled() is False


def test_feishu_markdown_commands_env_var(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MINIAGENT_FEISHU_MARKDOWN_COMMANDS=1 应覆盖 config=false。"""
    install_test_config(tmp_path, {"feishu": {"markdown_commands": False}})
    monkeypatch.setenv("MINIAGENT_FEISHU_MARKDOWN_COMMANDS", "1")
    assert feishu_markdown_commands_enabled() is True


def test_feishu_markdown_commands_string_false(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """字符串 ``\"false\"`` 应解析为关，避免 bool(\"false\") 误判。"""
    monkeypatch.delenv("MINIAGENT_FEISHU_MARKDOWN_COMMANDS", raising=False)
    install_test_config(tmp_path, {"feishu": {"markdown_commands": "false"}})
    assert feishu_markdown_commands_enabled() is False


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
