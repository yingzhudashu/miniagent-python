"""内置 ALL_TOOLS 注册与内置优先策略。"""

from __future__ import annotations

import pytest

from miniagent.engine.builtin_tools import register_builtin_tools
from miniagent.feishu.feishu_tool_policy import FEISHU_EXT_TOOL_NAMES
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


def test_register_builtin_tools_skips_feishu_ext_when_explicit_off(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "test_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test_secret")
    install_test_config(
        tmp_path,
        {"feishu": {"tools_explicit": False, "tools_auto": True}},
    )
    reg = DefaultToolRegistry()
    register_builtin_tools(reg)
    names = set(reg.list())
    assert not names & FEISHU_EXT_TOOL_NAMES
    assert "read_file" in names


def test_register_builtin_tools_skips_preexisting_names() -> None:
    """注册表已有同名工具时跳过该内置条目，继续注册其余工具。"""
    reg = DefaultToolRegistry()
    placeholder = ToolDefinition(
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "placeholder",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=lambda a, c: None,  # type: ignore[misc, assignment]
        permission="sandbox",
        help_text="",
    )
    reg.register("read_file", placeholder)
    n = register_builtin_tools(reg)
    assert n > 0
    existing = reg.get("read_file")
    assert existing is not None
    assert existing.schema["function"]["description"] == "placeholder"


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
