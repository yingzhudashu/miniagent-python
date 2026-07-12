"""无需 LLM 的确定性线性工具管线。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from miniagent.security.sandbox import get_default_workspace
from miniagent.types.agent import PipelineResult, PipelineStep, PipelineStepRecord, ToolCallResult
from miniagent.types.error_prefix import WARNING_PREFIX
from miniagent.types.tool import ToolContext, ToolRegistryProtocol


async def run_pipeline(
    steps: list[PipelineStep],
    registry: ToolRegistryProtocol,
    context: ToolContext | None = None,
    on_tool_call: Callable[[str, str, str], None] | None = None,
    *,
    clawhub: Any | None = None,
) -> PipelineResult:
    """按声明顺序执行工具，首次失败后停止且保留已完成记录。"""
    records: list[PipelineStepRecord] = []
    content = ""
    success = True
    if context is None:
        workspace = get_default_workspace()
        context = ToolContext(
            cwd=workspace, allowed_paths=[workspace], permission="allowlist", clawhub=clawhub
        )
    for step in steps:
        tool = registry.get(step.tool)
        if tool is None:
            result: ToolCallResult = {
                "success": False,
                "content": f"{WARNING_PREFIX} 未知工具: {step.tool}",
            }
            records.append({"tool": step.tool, "args": step.args, "result": result})
            return PipelineResult(steps=records, final_content=result["content"], success=False)
        tool_result = await tool.handler(step.args, context)
        records.append({
            "tool": step.tool,
            "args": step.args,
            "result": {"success": tool_result.success, "content": tool_result.content},
        })
        content += tool_result.content + "\n"
        if on_tool_call:
            on_tool_call(step.tool, json.dumps(step.args), tool_result.content)
        if not tool_result.success:
            success = False
            break
    return PipelineResult(steps=records, final_content=content.strip(), success=success)


__all__ = ["run_pipeline"]
