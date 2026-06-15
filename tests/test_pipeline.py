"""Tests for linear run_pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from miniagent.core.agent import run_pipeline
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.agent import PipelineStep
from miniagent.types.tool import ToolDefinition, ToolResult


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}},
    }


@pytest.mark.asyncio
async def test_pipeline_stops_on_tool_failure() -> None:
    registry = DefaultToolRegistry()

    async def ok_handler(_args, _ctx):
        return ToolResult(success=True, content="ok")

    async def fail_handler(_args, _ctx):
        return ToolResult(success=False, content="failed")

    registry.register(
        "ok_tool",
        ToolDefinition(
            schema=_schema("ok_tool"),
            handler=ok_handler,
            permission="allowlist",
            help_text="",
        ),
    )
    registry.register(
        "fail_tool",
        ToolDefinition(
            schema=_schema("fail_tool"),
            handler=fail_handler,
            permission="allowlist",
            help_text="",
        ),
    )

    result = await run_pipeline(
        [
            PipelineStep(tool="ok_tool"),
            PipelineStep(tool="fail_tool"),
            PipelineStep(tool="ok_tool"),
        ],
        registry=registry,
    )

    assert result.success is False
    assert len(result.steps) == 2
    assert result.steps[0]["result"]["success"] is True
    assert result.steps[1]["result"]["success"] is False
    assert "ok" in result.final_content
    assert "failed" in result.final_content


@pytest.mark.asyncio
async def test_pipeline_unknown_tool_fails() -> None:
    registry = DefaultToolRegistry()
    result = await run_pipeline([PipelineStep(tool="missing")], registry=registry)
    assert result.success is False
    assert len(result.steps) == 1
    assert "未知工具" in result.final_content


@pytest.mark.asyncio
async def test_pipeline_invokes_on_tool_call() -> None:
    registry = DefaultToolRegistry()

    async def ok_handler(args, _ctx):
        return ToolResult(success=True, content=f"got-{args.get('x', '')}")

    registry.register(
        "echo_tool",
        ToolDefinition(
            schema=_schema("echo_tool"),
            handler=ok_handler,
            permission="allowlist",
            help_text="",
        ),
    )

    calls: list[tuple[str, str, str]] = []

    def on_tool_call(tool: str, args_json: str, content: str) -> None:
        calls.append((tool, args_json, content))

    result = await run_pipeline(
        [PipelineStep(tool="echo_tool", args={"x": "hi"})],
        registry=registry,
        on_tool_call=on_tool_call,
    )

    assert result.success is True
    assert len(calls) == 1
    assert calls[0][0] == "echo_tool"
    assert '"x": "hi"' in calls[0][1] or '"x":"hi"' in calls[0][1].replace(" ", "")
    assert calls[0][2] == "got-hi"
