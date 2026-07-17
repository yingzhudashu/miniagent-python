"""Tests for the explicitly injected knowledge-base commands."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.commands.kb_commands import (
    cmd_kb_list,
    cmd_kb_mount,
    cmd_kb_reload,
    cmd_kb_search,
    cmd_kb_unmount,
    format_kb_command_usage,
)


def _capture(fn) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        fn()
    return buffer.getvalue()


def test_format_kb_command_usage_uses_slash_prefix() -> None:
    usage = format_kb_command_usage()
    assert "/kb list" in usage
    assert "/kb mount" in usage
    assert ".kb" not in usage


def test_cmd_kb_list_empty() -> None:
    registry = MagicMock()
    registry.list.return_value = []

    output = _capture(lambda: cmd_kb_list(registry))

    registry.list.assert_called_once_with()
    assert "/kb mount" in output


def test_cmd_kb_list_plain() -> None:
    registry = MagicMock()
    registry.list.return_value = [
        {"name": "docs", "entries": 3, "keywords": 12, "path": "/data/docs"},
    ]

    output = _capture(lambda: cmd_kb_list(registry))

    assert "docs" in output
    assert "3" in output
    assert "/data/docs" in output


def test_cmd_kb_list_markdown_escapes_pipe_in_path() -> None:
    registry = MagicMock()
    registry.list.return_value = [
        {"name": "weird", "entries": 1, "keywords": 2, "path": "/tmp/a|b"},
    ]

    output = _capture(lambda: cmd_kb_list(registry, markdown=True))

    assert "\\|" in output
    assert "| weird |" in output


def test_cmd_kb_mount_success() -> None:
    registry = MagicMock()
    registry.mount.return_value = {
        "success": True,
        "kb_name": "my_kb",
        "stats": {"entries": 5, "keywords": 20},
    }

    output = _capture(lambda: cmd_kb_mount(registry, "/path/to/kb", "my_kb"))

    registry.mount.assert_called_once_with("/path/to/kb", "my_kb")
    assert SUCCESS_PREFIX in output
    assert "my_kb" in output
    assert "5" in output


def test_cmd_kb_mount_failure() -> None:
    registry = MagicMock()
    registry.mount.return_value = {"success": False, "message": "missing path"}

    output = _capture(lambda: cmd_kb_mount(registry, "/missing"))

    assert ERROR_PREFIX in output
    assert "missing path" in output


def test_cmd_kb_unmount_success() -> None:
    registry = MagicMock()
    registry.unmount.return_value = {"success": True, "message": "removed docs"}

    output = _capture(lambda: cmd_kb_unmount(registry, "docs"))

    registry.unmount.assert_called_once_with("docs")
    assert SUCCESS_PREFIX in output
    assert "removed docs" in output


def test_cmd_kb_search_empty_query() -> None:
    registry = MagicMock()

    output = _capture(lambda: cmd_kb_search(registry, "   "))

    registry.search.assert_not_called()
    assert WARNING_PREFIX in output


def test_cmd_kb_search_no_results() -> None:
    registry = MagicMock()
    registry.search.return_value = ""

    output = _capture(lambda: cmd_kb_search(registry, "nothing"))

    registry.search.assert_called_once_with("nothing", kb_name=None)
    assert WARNING_PREFIX in output


def test_cmd_kb_search_with_results() -> None:
    registry = MagicMock()
    registry.search.return_value = "## API\n\nSome API docs."

    output = _capture(lambda: cmd_kb_search(registry, "API", "docs"))

    registry.search.assert_called_once_with("API", kb_name="docs")
    assert "Some API docs" in output


def test_cmd_kb_reload_all() -> None:
    registry = MagicMock()
    registry.reload.return_value = {"success": True, "message": "reloaded 2"}

    output = _capture(lambda: cmd_kb_reload(registry))

    registry.reload.assert_called_once_with(None)
    assert SUCCESS_PREFIX in output
    assert "2" in output


def test_cmd_kb_reload_single_failure() -> None:
    registry = MagicMock()
    registry.reload.return_value = {"success": False, "message": "not mounted: x"}

    output = _capture(lambda: cmd_kb_reload(registry, "x"))

    registry.reload.assert_called_once_with("x")
    assert ERROR_PREFIX in output
    assert "not mounted" in output
