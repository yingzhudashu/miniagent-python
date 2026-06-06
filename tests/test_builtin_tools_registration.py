"""内置 ALL_TOOLS 注册与内置优先策略。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from miniagent.engine.builtin_tools import register_builtin_tools
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.tool import ToolDefinition
from tests.config_helpers import install_test_config


def test_register_builtin_tools_populates_registry() -> None:
    reg = DefaultToolRegistry()
    n = register_builtin_tools(reg)
    assert n > 0
    names = reg.list()
    assert "read_file" in names
    assert "get_time" in names
    assert "manage_scheduled_task" in names
    # web_search/fetch_url/browser_extract_text 已移至 builtin-web skill，不再在 ALL_TOOLS
    assert "web_search" not in names
    assert "fetch_url" not in names
    assert "browser_extract_text" not in names
    # 新增 data_tools
    assert "read_csv" in names


def test_register_builtin_tools_skips_self_opt_when_disabled(tmp_path) -> None:
    install_test_config(tmp_path, {"self_optimization": {"enabled": True}})
    reg = DefaultToolRegistry()
    with patch("miniagent.core.constants.CLI_SELF_OPT_TOOLS", False):
        register_builtin_tools(reg)
    names = reg.list()
    assert "self_inspect" not in names
    assert "read_file" in names


def test_register_builtin_tools_skips_cli_dot_when_disabled(tmp_path) -> None:
    install_test_config(tmp_path, {"cli": {"dot_tools_enabled": False}})
    reg = DefaultToolRegistry()
    register_builtin_tools(reg)
    names = reg.list()
    assert "run_dot_command" not in names
    assert "read_file" in names


def test_register_builtin_tools_skips_schedule_tools_when_disabled(tmp_path) -> None:
    install_test_config(tmp_path, {"scheduled_tools": {"enabled": False}})
    reg = DefaultToolRegistry()
    register_builtin_tools(reg)
    names = reg.list()
    assert "manage_scheduled_task" not in names
    assert "read_file" in names


def test_register_builtin_tools_then_duplicate_register_raises() -> None:
    """内置先注册后，同名工具不得静默覆盖（须先 unregister）。"""
    reg = DefaultToolRegistry()
    register_builtin_tools(reg)
    dummy = ToolDefinition(
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "x",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=lambda a, c: None,  # type: ignore[misc, assignment]
        permission="sandbox",
        help_text="",
    )
    with pytest.raises(ValueError):
        reg.register("read_file", dummy)
