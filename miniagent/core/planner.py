"""Mini Agent Python — 规划器 (Phase 4)

两阶段 Agent 的规划阶段。调用 LLM 分析用户需求，生成结构化执行计划。
支持 3 次重试，全部失败时降级为 fallback 简单计划。

规划器默认使用 :func:`miniagent.core.openai_client.get_shared_async_openai`；
可通过 ``generate_plan(..., client=...)`` 注入测试桩或自定义客户端。
"""

from __future__ import annotations

import json
import os
from typing import Any

from miniagent.core.openai_client import get_shared_async_openai
from miniagent.types.planning import (
    StructuredPlan,
    PlanStep,
    SuggestedConfig,
    EstimatedTokens,
    ContextStrategy,
    EstimatedCost,
    OutputSpec,
    FallbackPlan,
)
from miniagent.types.tool import Toolbox
from miniagent.infrastructure.logger import append_log, truncate, get_logger

_logger = get_logger(__name__)

# ─── Agent 身份 ────────────────────────────────────────────

AGENT_NAME = "MiniAgent"

# ─── 常量 ───────────────────────────────────────────────

PLAN_SYSTEM_PROMPT = f"""你是 {AGENT_NAME} 的规划器。你是一个任务规划专家，负责分析用户需求并生成结构化的执行计划。

请以 JSON 格式返回计划，包含以下字段：
{{
  "summary": "计划摘要",
  "steps": [{{
      "stepNumber": 1, "description": "", "requiredToolboxes": [],
      "expectedInput": "", "expectedOutput": "", "dependsOn": null
  }}],
  "requiredToolboxes": [],
  "suggestedConfig": {{"maxTurns":5,"toolTimeout":30,"riskLevel":"low"}},
  "estimatedTokens": {{"promptTokens":500,"completionTokens":500,"toolResultTokens":200,"total":1200}},
  "contextStrategy": {{"mode":"normal","reason":""}},
  "requiresConfirmation": false,
  "riskLevel": "low",
  "estimatedCost": {{"inputTokens":0,"outputTokens":0,"totalUSD":0}},
  "outputSpec": {{"language":"zh-CN","format":"markdown","expectedDeliverable":""}},
  "fallbackPlan": {{"degradeToSimple":true,"degradedMaxTurns":5}}
}}

只返回 JSON，不要包含其他文字。"""

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_RETRIES = 3


# ─── 公共 API ───────────────────────────────────────────


async def generate_plan(
    user_input: str,
    toolboxes: list[Toolbox],
    log_file: str | None = None,
    *,
    client: Any | None = None,
) -> StructuredPlan:
    """根据用户需求和可用工具箱生成结构化执行计划。

    最多重试 MAX_RETRIES 次，全部失败返回 fallback plan。
    """
    toolboxes_json = json.dumps(
        [{"id": t.id, "name": t.name, "description": t.description, "keywords": t.keywords}
         for t in toolboxes],
        ensure_ascii=False,
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户需求: {user_input}\n\n可用工具箱:\n{toolboxes_json}"},
    ]

    llm_client = client if client is not None else get_shared_async_openai()

    for attempt in range(MAX_RETRIES):
        try:
            response = await llm_client.chat.completions.create(
                model=MODEL,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.3,
                max_tokens=2048,
            )

            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from planner")

            if log_file:
                append_log(log_file, {
                    "phase": "plan", "attempt": attempt + 1,
                    "req": {"model": MODEL, "messages": [
                        {"role": m["role"], "content": truncate(m.get("content", ""), 500)}
                        for m in messages
                    ]},
                    "res": {
                        "content": truncate(content, 2000),
                        "usage": response.usage.model_dump() if response.usage else None,
                    },
                })

            # 处理 markdown code block 包裹
            json_str = content.strip()
            if json_str.startswith("```"):
                json_str = json_str.replace("```json", "").replace("```", "").strip()

            plan_data: dict[str, Any] = json.loads(json_str)
            if "steps" not in plan_data or "requiredToolboxes" not in plan_data:
                raise ValueError("Invalid plan: missing required fields")

            return _dict_to_plan(plan_data)

        except Exception as e:
            _logger.warning("Planner attempt %d failed: %s", attempt + 1, e)
            if attempt == MAX_RETRIES - 1:
                return _fallback_plan(user_input)

    return _fallback_plan(user_input)


# ─── 内部辅助 ───────────────────────────────────────────


def _dict_to_plan(data: dict[str, Any]) -> StructuredPlan:
    """将 LLM 返回的 dict 转为 StructuredPlan。"""
    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        raw_steps = []

    def _step_as_dict(s: Any, idx: int) -> dict[str, Any]:
        if isinstance(s, dict):
            return s
        if isinstance(s, str):
            return {
                "stepNumber": idx,
                "description": s,
                "requiredToolboxes": [],
                "expectedInput": "",
                "expectedOutput": "",
                "dependsOn": None,
            }
        return {
            "stepNumber": idx,
            "description": str(s),
            "requiredToolboxes": [],
            "expectedInput": "",
            "expectedOutput": "",
            "dependsOn": None,
        }

    steps = [
        PlanStep(
            step_number=s.get("stepNumber", 0),
            description=s.get("description", ""),
            required_toolboxes=s.get("requiredToolboxes", []),
            expected_input=s.get("expectedInput", ""),
            expected_output=s.get("expectedOutput", ""),
            depends_on=s.get("dependsOn"),
        )
        for i, raw in enumerate(raw_steps, start=1)
        for s in (_step_as_dict(raw, i),)
    ]
    sc = data.get("suggestedConfig", {}) if isinstance(data.get("suggestedConfig"), dict) else {}
    et = data.get("estimatedTokens", {}) if isinstance(data.get("estimatedTokens"), dict) else {}
    cs = data.get("contextStrategy", {}) if isinstance(data.get("contextStrategy"), dict) else {}
    ec = data.get("estimatedCost", {}) if isinstance(data.get("estimatedCost"), dict) else {}
    osp = data.get("outputSpec", {}) if isinstance(data.get("outputSpec"), dict) else {}
    fb = data.get("fallbackPlan", {}) if isinstance(data.get("fallbackPlan"), dict) else {}

    return StructuredPlan(
        summary=data.get("summary", ""),
        steps=steps,
        required_toolboxes=data.get("requiredToolboxes", []),
        suggested_config=SuggestedConfig(
            max_turns=sc.get("maxTurns"),
            tool_timeout=sc.get("toolTimeout"),
            risk_level=sc.get("riskLevel"),
        ),
        estimated_tokens=EstimatedTokens(
            prompt_tokens=et.get("promptTokens", 500),
            completion_tokens=et.get("completionTokens", 500),
            tool_result_tokens=et.get("toolResultTokens", 200),
            total=et.get("total", 1200),
        ),
        context_strategy=ContextStrategy(
            mode=cs.get("mode", "normal"), reason=cs.get("reason", ""),
        ),
        requires_confirmation=data.get("requiresConfirmation", False),
        confirmation_message=data.get("confirmationMessage"),
        risk_level=data.get("riskLevel", "low"),
        estimated_cost=EstimatedCost(
            input_tokens=ec.get("inputTokens", 0),
            output_tokens=ec.get("outputTokens", 0),
            total_usd=ec.get("totalUSD", 0.0),
        ),
        output_spec=OutputSpec(
            language=osp.get("language", "zh-CN"),
            format=osp.get("format", "markdown"),
            expected_deliverable=osp.get("expectedDeliverable", ""),
        ),
        fallback_plan=FallbackPlan(
            degrade_to_simple=fb.get("degradeToSimple", True),
            degraded_max_turns=fb.get("degradedMaxTurns", 5),
        ),
    )


def _fallback_plan(user_input: str) -> StructuredPlan:
    """回退计划：跳过详细规划，直接执行。"""
    return StructuredPlan(
        summary="直接执行模式：跳过详细规划",
        steps=[PlanStep(
            step_number=1,
            description="根据用户需求直接处理",
            required_toolboxes=[],
            expected_input=user_input,
            expected_output="用户需求的回复",
        )],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=5, tool_timeout=30, risk_level="low"),
        estimated_tokens=EstimatedTokens(prompt_tokens=500, completion_tokens=500, tool_result_tokens=200, total=1200),
        context_strategy=ContextStrategy(mode="normal", reason="简单任务"),
        requires_confirmation=False,
        risk_level="low",
        estimated_cost=EstimatedCost(input_tokens=500, output_tokens=500, total_usd=0.0),
        output_spec=OutputSpec(language="zh-CN", format="markdown", expected_deliverable="直接回复"),
        fallback_plan=FallbackPlan(degrade_to_simple=False, degraded_max_turns=5),
    )


__all__ = ["generate_plan", "AGENT_NAME", "PLAN_SYSTEM_PROMPT"]
