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
import re
from typing import Any

from miniagent.core._openai_compat import json_object_unsupported as _json_object_unsupported
from miniagent.core.llm_json import parse_llm_json_response
from miniagent.core.openai_client import get_shared_async_openai
from miniagent.core.prompts.planner import PLAN_SYSTEM_PROMPT
from miniagent.infrastructure.debug_ndjson import safe_agent_debug_log
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

# ─── 常量 ───────────────────────────────────────────────

# PLAN_SYSTEM_PROMPT 现在从 miniagent.core.prompts.planner 导入
# 使用 XML 标签结构化，遵循 Claude 最佳实践

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

    RAG 增强：规划阶段会检索知识库（可选），注入到规划上下文，
    让规划器能判断是否需要 "knowledge" 工具箱。
    """
    from miniagent.core.llm_params import resolve_planner_completion_kwargs
    from miniagent.infrastructure.tracing import emit_trace
    from miniagent.knowledge import retrieve_knowledge_context
    from miniagent.types.config import AgentConfig

    ac: AgentConfig | None = agent_config if isinstance(agent_config, AgentConfig) else None
    planner_kw = resolve_planner_completion_kwargs(ac, merge_overrides=planner_model_overrides)

    # ── RAG 增强：知识库检索（使用公共函数）──
    kb_context_planner = retrieve_knowledge_context(
        user_input, phase="planner", default_top_k=2, default_max_chars=2000
    )

    toolboxes_json = json.dumps(
        [
            {"id": t.id, "name": t.name, "description": t.description, "keywords": t.keywords}
            for t in toolboxes
        ],
        ensure_ascii=False,
    )

    toolbox_ids = [t.id for t in toolboxes]
    tool_hint = _format_toolbox_tool_names(registry, toolbox_ids)
    user_parts = [
        f"用户需求: {user_input}",
        f"可用工具箱:\n{toolboxes_json}",
    ]
    if tool_hint:
        user_parts.append(
            f"各工具箱内可用工具名称（规划时请对齐 requiredToolboxes 与工具箱 id）:\n{tool_hint}"
        )
    # 注入知识库检索结果
    if kb_context_planner:
        user_parts.append(kb_context_planner)
    completed_context = _completed_work_context(ac)
    if completed_context:
        user_parts.append(completed_context)

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

            emit_trace(
                {
                    "type": "llm.request",
                    "phase": "plan",
                    "attempt": attempt + 1,
                    "model": planner_kw["model"],
                    "json_object": use_json_object,
                }
            )
            safe_agent_debug_log(
                location="planner.py:generate_plan",
                message="before_planner_chat_completions",
                data={
                    "attempt": attempt + 1,
                    "model": planner_kw.get("model"),
                    "json_object": use_json_object,
                },
            )
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

            emit_trace(
                {
                    "type": "llm.response",
                    "phase": "plan",
                    "attempt": attempt + 1,
                    "model": planner_kw["model"],
                    "usage": response.usage.model_dump() if response.usage else None,
                }
            )

            if log_file:
                append_log(
                    log_file,
                    {
                        "phase": "plan",
                        "attempt": attempt + 1,
                        "req": {
                            "model": planner_kw["model"],
                            "messages": [
                                {"role": m["role"], "content": truncate(m.get("content", ""), 500)}
                                for m in messages
                            ],
                        },
                        "res": {
                            "content": truncate(content, 2000),
                            "usage": response.usage.model_dump() if response.usage else None,
                        },
                    },
                )

            plan_data = parse_llm_json_response(content)
            if "steps" not in plan_data or "requiredToolboxes" not in plan_data:
                raise ValueError("Invalid plan: missing required fields")

            plan = _dict_to_plan(plan_data, default_step_thinking=default_step_thinking)
            return plan

        except Exception as e:
            safe_agent_debug_log(
                location="planner.py:generate_plan",
                message="planner_attempt_failed",
                data={
                    "attempt": attempt + 1,
                    "exc_type": type(e).__name__,
                    "exc_msg": str(e)[:400],
                },
            )
            _logger.warning("Planner attempt %d failed: %s", attempt + 1, e)
            if attempt == MAX_RETRIES - 1:
                return _fallback_plan(user_input)

    # 不可达：循环内最后一轮必定返回
    assert False, "unreachable"


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


def _completed_work_context(agent_config: Any | None) -> str:
    """Summarize recent completed work so the planner can reuse it."""
    history = getattr(agent_config, "conversation_history", None) if agent_config is not None else None
    if not history:
        return ""
    lines: list[str] = []
    for msg in history[-20:]:
        content = str(msg.get("content", "")) if isinstance(msg, dict) else ""
        if not content:
            continue
        low = content.lower()
        if any(term in low for term in ("read_file", "已读取", "分析", "测试", "pytest", "已完成", "rag", "知识库")):
            lines.append(f"- {content[:180]}")
    if not lines:
        return ""
    return "## 最近已完成工作（规划时应复用，避免重复步骤）\n" + "\n".join(lines[-8:])


def _dict_to_plan(data: dict[str, Any], *, default_step_thinking: str = "medium") -> StructuredPlan:
    """将 LLM 返回的 dict 转为 StructuredPlan。

    解析流程：
    1. 步骤列表（steps）→ List[PlanStep]，支持 dict/str/其它三种输入格式
    2. 嵌套配置（suggestedConfig 等）→ 各字段安全提取，空值回退默认
    3. 组装为 StructuredPlan dataclass
    """
    # ── 步骤解析 ───────────────────────────────────────────────
    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        raw_steps = []

    # 默认 thinking 档位：LLM 未指定时使用参数传入值
    step_fallback = str(data.get("defaultStepThinkingLevel") or default_step_thinking)

    def _step_as_dict(s: Any, idx: int) -> dict[str, Any]:
        """将原始步骤项（dict / str / 其它）规范为规划步骤字段字典。

        LLM 可能返回：
        - dict: 完整结构，直接使用
        - str: 仅描述，自动填充默认字段
        - 其它: 强制转 str，按 str 处理
        """
        if isinstance(s, dict):
            return s
        # str 或其它类型：统一转为描述文本（str 本身 str() 不变），填充默认字段
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
        """解析单步 ``thinkingLevel``，缺省则回落 ``step_fallback``。

        thinkingLevel 取值：low / medium / high，影响该步的推理深度。
        """
        tl = s.get("thinkingLevel")
        if tl is None or tl == "":
            return step_fallback
        return str(tl)

    steps: list[PlanStep] = []
    for i, raw in enumerate(raw_steps, start=1):
        s = _step_as_dict(raw, i)
        steps.append(
            PlanStep(
                step_number=s.get("stepNumber", 0),
                description=s.get("description", ""),
                required_toolboxes=s.get("requiredToolboxes", []),
                expected_input=s.get("expectedInput", ""),
                expected_output=s.get("expectedOutput", ""),
                depends_on=s.get("dependsOn"),
                thinking_level=_step_thinking_level(s),
            )
        )
    steps = _normalize_plan_steps(steps)

    # ── 嵌套配置解析（安全提取，空值回退）───────────────────────────────
    # suggestedConfig: 执行建议（轮数、超时、风险等级、策略等）
    sc = data.get("suggestedConfig", {}) if isinstance(data.get("suggestedConfig"), dict) else {}
    # estimatedTokens: Token 预估（用于成本监控）
    et = data.get("estimatedTokens", {}) if isinstance(data.get("estimatedTokens"), dict) else {}
    # contextStrategy: 上下文处理策略（溢出时的压缩/摘要行为）
    cs = data.get("contextStrategy", {}) if isinstance(data.get("contextStrategy"), dict) else {}
    # estimatedCost: 成本预估（USD）
    ec = data.get("estimatedCost", {}) if isinstance(data.get("estimatedCost"), dict) else {}
    # outputSpec: 输出规格（语言、格式、交付物）
    osp = data.get("outputSpec", {}) if isinstance(data.get("outputSpec"), dict) else {}
    # fallbackPlan: 降级计划（规划失败时的执行策略）
    fb = data.get("fallbackPlan", {}) if isinstance(data.get("fallbackPlan"), dict) else {}

    return StructuredPlan(
        summary=data.get("summary", ""),
        steps=steps,
        required_toolboxes=_dedupe_toolboxes(data.get("requiredToolboxes", []), steps),
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
            mode=cs.get("mode", "normal"),
            reason=cs.get("reason", ""),
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


def _normalize_plan_steps(steps: list[PlanStep]) -> list[PlanStep]:
    """Remove duplicate/empty plan steps and repair numbering/dependencies."""
    kept: list[PlanStep] = []
    old_to_new: dict[int, int] = {}
    fingerprint_to_new: dict[str, int] = {}

    for original_index, step in enumerate(steps, start=1):
        original_number = int(step.step_number or original_index)
        step.description = str(step.description or "").strip()
        step.expected_input = str(step.expected_input or "").strip()
        step.expected_output = str(step.expected_output or "").strip()
        step.required_toolboxes = _unique_strings(step.required_toolboxes)
        if not step.description and not step.expected_output:
            continue
        fingerprint = _step_fingerprint(step)
        duplicate_new = fingerprint_to_new.get(fingerprint)
        if duplicate_new is not None:
            old_to_new[original_number] = duplicate_new
            continue
        new_number = len(kept) + 1
        old_to_new[original_number] = new_number
        fingerprint_to_new[fingerprint] = new_number
        step.step_number = new_number
        kept.append(step)

    for step in kept:
        if step.depends_on is None:
            continue
        try:
            dep = int(step.depends_on)
        except (TypeError, ValueError):
            step.depends_on = None
            continue
        mapped = old_to_new.get(dep)
        step.depends_on = mapped if mapped and mapped != step.step_number else None
    return kept


def _dedupe_toolboxes(raw_toolboxes: Any, steps: list[PlanStep]) -> list[str]:
    values: list[str] = []
    if isinstance(raw_toolboxes, list):
        values.extend(str(item) for item in raw_toolboxes if str(item).strip())
    for step in steps:
        values.extend(step.required_toolboxes)
    return _unique_strings(values)


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _step_fingerprint(step: PlanStep) -> str:
    text = " ".join([step.description, step.expected_input, step.expected_output]).lower()
    path = _first_path_like(text)
    action = _action_bucket(text)
    toolboxes = ",".join(sorted(step.required_toolboxes))
    if path:
        return f"{action}|{path}|{toolboxes}"
    normalized = re.sub(r"\s+", "", text)
    return f"{action}|{normalized[:80]}|{toolboxes}"


def _first_path_like(text: str) -> str:
    match = re.search(r"([a-zA-Z0-9_.\\/-]+\.(?:py|md|txt|json|ya?ml|toml|ini|csv|html|css|js|ts))", text)
    if match:
        return match.group(1).replace("\\", "/").lower()
    return ""


def _action_bucket(text: str) -> str:
    if any(term in text for term in ("读取", "read", "查看", "打开")):
        return "read"
    if any(term in text for term in ("扫描", "查找", "list", "搜索")):
        return "discover"
    if any(term in text for term in ("分析", "审查", "总结", "解释", "review", "analy")):
        return "analyze"
    if any(term in text for term in ("测试", "验证", "pytest", "compile", "ruff")):
        return "verify"
    return "work"


def _fallback_plan(user_input: str) -> StructuredPlan:
    """回退计划：跳过详细规划，直接执行。"""
    return StructuredPlan(
        summary="直接执行模式：跳过详细规划",
        steps=[
            PlanStep(
                step_number=1,
                description="根据用户需求直接处理",
                required_toolboxes=[],
                expected_input=user_input,
                expected_output="用户需求的回复",
                thinking_level="low",
            )
        ],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=5, tool_timeout=30, risk_level="low"),
        estimated_tokens=EstimatedTokens(
            prompt_tokens=500, completion_tokens=500, tool_result_tokens=200, total=1200
        ),
        context_strategy=ContextStrategy(mode="normal", reason="简单任务"),
        requires_confirmation=False,
        risk_level="low",
        estimated_cost=EstimatedCost(input_tokens=500, output_tokens=500, total_usd=0.0),
        output_spec=OutputSpec(
            language="zh-CN", format="markdown", expected_deliverable="直接回复"
        ),
        fallback_plan=FallbackPlan(degrade_to_simple=False, degraded_max_turns=5),
    )


__all__ = ["generate_plan", "_normalize_plan_steps"]
