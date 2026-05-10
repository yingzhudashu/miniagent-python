"""tool_selection_strategy=auto 的工具列表语义。"""

from __future__ import annotations

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.config import AgentConfig
from miniagent.types.planning import StructuredPlan
from miniagent.types.tool import ToolDefinition, ToolResult


async def _h(args: dict, ctx) -> ToolResult:
    return ToolResult(True, "")


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": "d", "parameters": {"type": "object", "properties": {}}},
    }


def test_auto_without_required_toolboxes_is_core_only() -> None:
    reg = DefaultToolRegistry()
    reg.register(
        "core_one",
        ToolDefinition(
            schema=_schema("core_one"),
            handler=_h,
            permission="allowlist",
            help_text="",
            toolbox=None,
        ),
    )
    reg.register(
        "boxed",
        ToolDefinition(
            schema=_schema("boxed"),
            handler=_h,
            permission="allowlist",
            help_text="",
            toolbox="file_read",
        ),
    )
    plan = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
    ac = AgentConfig(tool_selection_strategy="auto")
    if ac.tool_selection_strategy == "auto":
        if plan.required_toolboxes:
            tools = reg.get_schemas_by_toolboxes(plan.required_toolboxes)
        else:
            tools = [t.schema for t in reg.get_all().values() if t.toolbox is None]
            if not tools:
                tools = reg.get_schemas()
    else:
        tools = []
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "core_one"


def test_auto_with_required_merges_like_toolbox_mode() -> None:
    reg = DefaultToolRegistry()
    reg.register(
        "core_one",
        ToolDefinition(
            schema=_schema("core_one"),
            handler=_h,
            permission="allowlist",
            help_text="",
            toolbox=None,
        ),
    )
    reg.register(
        "fr",
        ToolDefinition(
            schema=_schema("fr"),
            handler=_h,
            permission="allowlist",
            help_text="",
            toolbox="file_read",
        ),
    )
    plan = StructuredPlan(summary="s", steps=[], required_toolboxes=["file_read"])
    ac = AgentConfig(tool_selection_strategy="auto")
    if ac.tool_selection_strategy == "auto" and plan.required_toolboxes:
        tools = reg.get_schemas_by_toolboxes(plan.required_toolboxes)
    else:
        tools = []
    names = {t["function"]["name"] for t in tools}
    assert "core_one" in names
    assert "fr" in names
