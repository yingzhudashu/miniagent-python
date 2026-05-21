"""Mini Agent Python — 规划器（两阶段中的规划阶段）

调用 LLM 分析用户需求，生成结构化执行计划（``StructuredPlan``）。系统提示要求模型返回 **单一 JSON 对象**
（与 ``response_format`` / json_object 模式对齐）；解析失败或网络错误时最多重试 ``MAX_RETRIES`` 次，
全部失败则降级为内置 fallback 计划，保证执行阶段始终有可消费的结构。

``planner_model_overrides`` 与 :func:`miniagent.core.llm_params.resolve_planner_completion_kwargs` 合并，
用于低温、较小 ``max_tokens`` 等规划专用参数。默认客户端为
:func:`miniagent.core.openai_client.get_shared_async_openai`；测试可通过 ``generate_plan(..., client=...)`` 注入桩。

输出契约与 Phase 2 消费方式见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import json
import os
from typing import Any

from miniagent.core.openai_client import get_shared_async_openai
from miniagent.infrastructure.logger import append_log, get_logger, truncate
from miniagent.types.planning import (
    ContextStrategy,
    EstimatedCost,
    EstimatedTokens,
    FallbackPlan,
    OutputSpec,
    PlanStep,
    StructuredPlan,
    SuggestedConfig,
)
from miniagent.types.tool import Toolbox

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
      "expectedInput": "", "expectedOutput": "", "dependsOn": null,
      "thinkingLevel": "medium"
  }}],
  "requiredToolboxes": [],
  "defaultStepThinkingLevel": "medium",
  "suggestedConfig": {{"maxTurns":5,"toolTimeout":30,"riskLevel":"low","contextOverflowStrategy":"summarize","toolSelectionStrategy":"toolbox","modelOverrides":{{}},"parallelism":"safe-parallel"}},
  "estimatedTokens": {{"promptTokens":500,"completionTokens":500,"toolResultTokens":200,"total":1200}},
  "contextStrategy": {{"mode":"normal","reason":""}},
  "requiresConfirmation": false,
  "riskLevel": "low",
  "estimatedCost": {{"inputTokens":0,"outputTokens":0,"totalUSD":0}},
  "outputSpec": {{"language":"zh-CN","format":"markdown","expectedDeliverable":""}},
  "fallbackPlan": {{"degradeToSimple":true,"degradedMaxTurns":5}}
}}

只返回 JSON，不要包含其他文字。
若 API 使用 json_object 模式，响应体须为单个 JSON 对象（即上述结构本身，不要数组或额外键）。
每个步骤必须包含 thinkingLevel，取值 low / medium / high（与任务该步所需推理深度一致）。
涉及时效数据、客观事实或天气等问题时，顶层 requiredToolboxes 应包含 \"web\"（内含 web_search、browser_extract_text、fetch_url）。"""

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
from miniagent.core._openai_compat import json_object_unsupported as _json_object_unsupported

MAX_RETRIES = 3


# ─── 公共 API ───────────────────────────────────────────


async def generate_plan(
    user_input: str,
    toolboxes: list[Toolbox],
    log_file: str | None = None,
    *,
    client: Any | None = None,
    agent_config: Any | None = None,
    registry: Any | None = None,
    planner_model_overrides: dict[str, Any] | None = None,
    default_step_thinking: str = "medium",
) -> StructuredPlan:
    """根据用户需求和可用工具箱生成结构化执行计划。

    最多重试 MAX_RETRIES 次，全部失败返回 fallback plan。
    """
    from miniagent.core.llm_params import resolve_planner_completion_kwargs
    from miniagent.infrastructure.tracing import emit_trace
    from miniagent.types.config import AgentConfig

    ac: AgentConfig | None = agent_config if isinstance(agent_config, AgentConfig) else None
    planner_kw = resolve_planner_completion_kwargs(ac, merge_overrides=planner_model_overrides)

    toolboxes_json = json.dumps(
        [{"id": t.id, "name": t.name, "description": t.description, "keywords": t.keywords}
         for t in toolboxes],
        ensure_ascii=False,
    )

    toolbox_ids = [t.id for t in toolboxes]
    tool_hint = _format_toolbox_tool_names(registry, toolbox_ids)
    user_parts = [
        f"用户需求: {user_input}",
        f"可用工具箱:\n{toolboxes_json}",
    ]
    if tool_hint:
        user_parts.append(f"各工具箱内可用工具名称（规划时请对齐 requiredToolboxes 与工具箱 id）:\n{tool_hint}")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    llm_client = client if client is not None else get_shared_async_openai()
    use_json_object = True

    for attempt in range(MAX_RETRIES):
        try:
            create_args: dict[str, Any] = {
                "messages": messages,  # type: ignore[arg-type]
                **planner_kw,
            }
            if use_json_object:
                create_args["response_format"] = {"type": "json_object"}

            emit_trace({
                "type": "llm.request",
                "phase": "plan",
                "attempt": attempt + 1,
                "model": planner_kw["model"],
                "json_object": use_json_object,
            })
            # #region agent log
            try:
                from miniagent.infrastructure.debug_ndjson import agent_debug_log

                agent_debug_log(
                    hypothesis_id="B",
                    location="planner.py:generate_plan",
                    message="before_planner_chat_completions",
                    data={"attempt": attempt + 1, "model": planner_kw.get("model"), "json_object": use_json_object},
                )
            except Exception:
                pass
            # #endregion
            try:
                response = await llm_client.chat.completions.create(**create_args)
            except Exception as api_err:
                if use_json_object and _json_object_unsupported(api_err):
                    use_json_object = False
                    _logger.info("Planner: API 不支持 response_format json_object，已降级")
                    response = await llm_client.chat.completions.create(
                        messages=messages,  # type: ignore[arg-type]
                        **planner_kw,
                    )
                else:
                    raise

            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from planner")

            emit_trace({
                "type": "llm.response",
                "phase": "plan",
                "attempt": attempt + 1,
                "model": planner_kw["model"],
                "usage": response.usage.model_dump() if response.usage else None,
            })

            if log_file:
                append_log(log_file, {
                    "phase": "plan", "attempt": attempt + 1,
                    "req": {"model": planner_kw["model"], "messages": [
                        {"role": m["role"], "content": truncate(m.get("content", ""), 500)}
                        for m in messages
                    ]},
                    "res": {
                        "content": truncate(content, 2000),
                        "usage": response.usage.model_dump() if response.usage else None,
                    },
                })

            plan_data = _parse_plan_json(content)
            if "steps" not in plan_data or "requiredToolboxes" not in plan_data:
                raise ValueError("Invalid plan: missing required fields")

            return _dict_to_plan(plan_data, default_step_thinking=default_step_thinking)

        except Exception as e:
            # #region agent log
            try:
                from miniagent.infrastructure.debug_ndjson import agent_debug_log

                agent_debug_log(
                    hypothesis_id="B",
                    location="planner.py:generate_plan",
                    message="planner_attempt_failed",
                    data={
                        "attempt": attempt + 1,
                        "exc_type": type(e).__name__,
                        "exc_msg": str(e)[:400],
                    },
                )
            except Exception:
                pass
            # #endregion
            _logger.warning("Planner attempt %d failed: %s", attempt + 1, e)
            if attempt == MAX_RETRIES - 1:
                return _fallback_plan(user_input)

    return _fallback_plan(user_input)


# ─── 内部辅助 ───────────────────────────────────────────


def _format_toolbox_tool_names(registry: Any, toolbox_ids: list[str]) -> str:
    """按工具箱 id 列出注册表中的工具名（无注册表或为空则返回空串）。"""
    if registry is None or not toolbox_ids:
        return ""
    try:
        all_tools = registry.get_all()
    except Exception:
        return ""
    by_tb: dict[str, list[str]] = {}
    core: list[str] = []
    for name, t in all_tools.items():
        tb = t.toolbox
        if tb is None:
            core.append(name)
        else:
            by_tb.setdefault(str(tb), []).append(name)
    lines: list[str] = []
    if core:
        lines.append(f"__core__（无工具箱绑定的核心工具）: {', '.join(sorted(core))}")
    for tid in sorted(set(toolbox_ids)):
        names = sorted(by_tb.get(tid, []))
        lines.append(f"{tid}: {', '.join(names) if names else '(无匹配工具)'}")
    return "\n".join(lines)


def _parse_plan_json(content: str) -> dict[str, Any]:
    """解析规划器输出：去 markdown 围栏、截取首尾大括号、json.loads。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _dict_to_plan(data: dict[str, Any], *, default_step_thinking: str = "medium") -> StructuredPlan:
    """将 LLM 返回的 dict 转为 StructuredPlan。"""
    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        raw_steps = []

    step_fallback = str(data.get("defaultStepThinkingLevel") or default_step_thinking)

    def _step_as_dict(s: Any, idx: int) -> dict[str, Any]:
        """将原始步骤项（dict / str / 其它）规范为规划步骤字段字典。"""
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
                "thinkingLevel": None,
            }
        return {
            "stepNumber": idx,
            "description": str(s),
            "requiredToolboxes": [],
            "expectedInput": "",
            "expectedOutput": "",
            "dependsOn": None,
            "thinkingLevel": None,
        }

    def _step_thinking_level(s: dict[str, Any]) -> str | None:
        """解析单步 ``thinkingLevel``，缺省则回落 ``step_fallback``。"""
        tl = s.get("thinkingLevel")
        if tl is None or tl == "":
            return step_fallback
        return str(tl)

    steps = [
        PlanStep(
            step_number=s.get("stepNumber", 0),
            description=s.get("description", ""),
            required_toolboxes=s.get("requiredToolboxes", []),
            expected_input=s.get("expectedInput", ""),
            expected_output=s.get("expectedOutput", ""),
            depends_on=s.get("dependsOn"),
            thinking_level=_step_thinking_level(s),
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
            context_overflow_strategy=sc.get("contextOverflowStrategy"),
            tool_selection_strategy=sc.get("toolSelectionStrategy"),
            model_overrides=sc.get("modelOverrides")
            if isinstance(sc.get("modelOverrides"), dict)
            else None,
            thinking_level=sc.get("thinkingLevel"),
            chunk_execution=bool(sc.get("chunkExecution", False)),
            chunk_token_budget=sc.get("chunkTokenBudget"),
            parallelism=sc.get("parallelism"),
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
            thinking_level="low",
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
