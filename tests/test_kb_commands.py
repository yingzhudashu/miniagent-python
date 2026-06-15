"""Tests for kb_commands module."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from miniagent.engine.commands.kb_commands import (
    cmd_kb_list,
    cmd_kb_mount,
    cmd_kb_reload,
    cmd_kb_search,
    cmd_kb_unmount,
    format_kb_command_usage,
)
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def _capture(fn) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return buf.getvalue()


def test_format_kb_command_usage_uses_slash_prefix() -> None:
    usage = format_kb_command_usage()
    assert "/kb list" in usage
    assert "/kb mount" in usage
    assert ".kb" not in usage


def test_cmd_kb_list_empty() -> None:
    registry = MagicMock()
    registry.list.return_value = []

    with patch("miniagent.knowledge.get_kb_registry", return_value=registry):
        out = _capture(lambda: cmd_kb_list())

    assert "未挂载" in out
    assert "/kb mount" in out


def test_cmd_kb_list_plain() -> None:
    registry = MagicMock()
    registry.list.return_value = [
        {"name": "docs", "entries": 3, "keywords": 12, "path": "/data/docs"},
    ]

    with patch("miniagent.knowledge.get_kb_registry", return_value=registry):
        out = _capture(lambda: cmd_kb_list())

    assert "docs" in out
    assert "3 条目" in out
    assert "/data/docs" in out


def test_cmd_kb_list_markdown_escapes_pipe_in_path() -> None:
    registry = MagicMock()
    registry.list.return_value = [
        {
            "name": "weird",
            "entries": 1,
            "keywords": 2,
            "path": "/tmp/a|b",
        },
    ]

    with patch("miniagent.knowledge.get_kb_registry", return_value=registry):
        out = _capture(lambda: cmd_kb_list(markdown=True))

    assert "\\|" in out
    assert "| weird |" in out


def test_cmd_kb_mount_success() -> None:
    with patch(
        "miniagent.knowledge.mount_knowledge_base",
        return_value={
            "success": True,
            "kb_name": "my_kb",
            "stats": {"entries": 5, "keywords": 20},
        },
    ):
        out = _capture(lambda: cmd_kb_mount("/path/to/kb", "my_kb"))

    assert SUCCESS_PREFIX in out
    assert "my_kb" in out
    assert "5" in out


def test_cmd_kb_mount_failure() -> None:
    with patch(
        "miniagent.knowledge.mount_knowledge_base",
        return_value={"success": False, "message": "路径不存在"},
    ):
        out = _capture(lambda: cmd_kb_mount("/missing"))

    assert ERROR_PREFIX in out
    assert "路径不存在" in out


def test_cmd_kb_unmount_success() -> None:
    with patch(
        "miniagent.knowledge.unmount_knowledge_base",
        return_value={"success": True, "message": "已卸载知识库: docs"},
    ):
        out = _capture(lambda: cmd_kb_unmount("docs"))

    assert SUCCESS_PREFIX in out
    assert "已卸载" in out


def test_cmd_kb_search_empty_query() -> None:
    with patch("miniagent.knowledge.search_knowledge") as mock_search:
        out = _capture(lambda: cmd_kb_search("   "))

    mock_search.assert_not_called()
    assert WARNING_PREFIX in out
    assert "搜索关键词" in out


def test_cmd_kb_search_no_results() -> None:
    with patch("miniagent.knowledge.search_knowledge", return_value=""):
        out = _capture(lambda: cmd_kb_search("nothing"))

    assert WARNING_PREFIX in out
    assert "未找到" in out


def test_cmd_kb_search_with_results() -> None:
    with patch(
        "miniagent.knowledge.search_knowledge",
        return_value="## API\n\nSome API docs.",
    ):
        out = _capture(lambda: cmd_kb_search("API", "docs"))

    assert "API" in out
    assert "Some API docs" in out


def test_cmd_kb_search_unmounted_kb_warning() -> None:
    warning = f"{WARNING_PREFIX} 知识库 'missing' 未挂载"
    with patch("miniagent.knowledge.search_knowledge", return_value=warning):
        out = _capture(lambda: cmd_kb_search("q", "missing"))

    assert "未挂载" in out


def test_cmd_kb_reload_all() -> None:
    registry = MagicMock()
    registry.reload.return_value = {
        "success": True,
        "message": "已重载 2 个知识库",
    }

    with patch("miniagent.knowledge.get_kb_registry", return_value=registry):
        out = _capture(lambda: cmd_kb_reload())

    registry.reload.assert_called_once_with(None)
    assert SUCCESS_PREFIX in out
    assert "2" in out


def test_cmd_kb_reload_single_failure() -> None:
    registry = MagicMock()
    registry.reload.return_value = {
        "success": False,
        "message": "知识库 'x' 未挂载",
    }

    with patch("miniagent.knowledge.get_kb_registry", return_value=registry):
        out = _capture(lambda: cmd_kb_reload("x"))

    assert ERROR_PREFIX in out
    assert "未挂载" in out
