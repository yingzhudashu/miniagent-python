"""执行器 system prompt 合并与规划 JSON 解析。"""

from __future__ import annotations

from miniagent.core.executor import build_execution_system_prompt
from miniagent.core.planner import _format_toolbox_tool_names, _parse_plan_json
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.tool import ToolDefinition


def test_build_execution_system_prompt_order() -> None:
    s = build_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt="SKILL",
        plan_summary="TASK",
        keyword_context="KW",
    )
    assert s.index("ID") < s.index("SKILL") < s.index("当前任务：TASK") < s.index("KW")


def test_build_execution_system_prompt_skips_empty_caller() -> None:
    s = build_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt=None,
        plan_summary="T",
        keyword_context=None,
    )
    assert "ID" in s
    assert "当前任务：T" in s
    assert s.count("\n\n") >= 1
    assert "默认文件根目录" not in s


def test_build_execution_system_prompt_session_files_root_blank_ignored() -> None:
    s = build_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt=None,
        plan_summary="T",
        keyword_context=None,
        session_files_root="   ",
    )
    assert "默认文件根目录" not in s


def test_parse_plan_json_brace_slice() -> None:
    raw = '说明文字\n{"summary":"x","steps":[],"requiredToolboxes":[]}\n尾部'
    data = _parse_plan_json(raw)
    assert data["summary"] == "x"
    assert data["requiredToolboxes"] == []


def test_format_toolbox_tool_names() -> None:
    reg = DefaultToolRegistry()

    async def _h(args, ctx):
        from miniagent.types.tool import ToolResult

        return ToolResult(True, "")

    reg.register(
        "read_file",
        ToolDefinition(
            schema={"type": "function", "function": {"name": "read_file", "parameters": {}}},
            handler=_h,
            permission="sandbox",
            help_text="",
            toolbox="file_read",
        ),
    )
    reg.register(
        "noop",
        ToolDefinition(
            schema={"type": "function", "function": {"name": "noop", "parameters": {}}},
            handler=_h,
            permission="sandbox",
            help_text="",
            toolbox=None,
        ),
    )
    hint = _format_toolbox_tool_names(reg, ["file_read"])
    assert "read_file" in hint
    assert "__core__" in hint
    assert "noop" in hint


