"""Mini Agent Python — 规划器（两阶段中的规划阶段）

调用 LLM 分析用户需求，生成结构化执行计划（``StructuredPlan``）。系统提示要求模型返回 **单一 JSON 对象**
（与 ``response_format`` / json_object 模式对齐）；解析失败或网络错误时最多重试 ``PLANNER_MAX_RETRIES`` 次。
Responses 空文本会按 reasoning-only、截断和 completed-empty 分类并有界恢复，全部失败才降级为内置
fallback 计划，保证执行阶段始终有可消费的结构。

``planner_model_overrides`` 与 :func:`miniagent.core.llm_params.resolve_planner_completion_kwargs` 合并，
用于低温、较小 ``max_tokens`` 等规划专用参数。LLM 客户端由组合根创建，
并通过 ``generate_plan(..., client=...)`` 显式注入。

输出契约与 Phase 2 消费方式见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, cast

from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
from miniagent.core._openai_compat import (
    ensure_json_object_user_message,
)
from miniagent.core._openai_compat import (
    json_object_unsupported as _json_object_unsupported,
)
from miniagent.core.constants import PLANNER_MAX_RETRIES
from miniagent.core.llm_json import parse_llm_json_response
from miniagent.core.llm_transport import (
    LLMCompletion,
    classify_transport_error,
    completion_failure_category,
    create_completion,
    create_structured_completion,
    structured_retry_delay,
    structured_retry_params,
)
from miniagent.core.plan_utils import parse_plan_chunks_from_raw, parse_plan_steps_from_raw
from miniagent.core.planner_support import (
    completed_work_context as _completed_work_context,
)
from miniagent.core.planner_support import (
    fallback_plan as _fallback_plan,
)
from miniagent.core.planner_support import (
    format_toolbox_tool_names as _format_toolbox_tool_names,
)
from miniagent.core.prompts.planner import PLAN_SYSTEM_PROMPT
from miniagent.infrastructure.debug_ndjson import safe_agent_debug_log
from miniagent.infrastructure.logger import append_log, get_logger, truncate
from miniagent.types.config import AgentConfig, WireAPI
from miniagent.types.planning import (
    ContextMode,
    ContextStrategy,
    EstimatedCost,
    EstimatedTokens,
    FallbackPlan,
    OutputFormat,
    OutputSpec,
    PlanChunk,
    PlanStep,
    StructuredPlan,
    SuggestedConfig,
    ThinkingLevel,
)
from miniagent.types.tool import Toolbox

_logger = get_logger(__name__)


class _PlannerAttemptFailure(RuntimeError):
    """Safe, structured failure raised within one planner attempt."""

    def __init__(
        self,
        category: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
        incomplete_reason: str | None = None,
    ) -> None:
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.incomplete_reason = incomplete_reason


def _build_planner_messages(
    user_input: str,
    toolboxes: list[Toolbox],
    *,
    registry: Any | None,
    knowledge_registry: KnowledgeRegistryProtocol,
    agent_config: AgentConfig | None,
) -> list[dict[str, str]]:
    """构建规划器上下文，合并工具、知识库与已完成工作摘要。"""
    from miniagent.knowledge import retrieve_knowledge_context

    toolbox_data = [
        {
            "id": toolbox.id,
            "name": toolbox.name,
            "description": toolbox.description,
            "keywords": toolbox.keywords,
        }
        for toolbox in toolboxes
    ]
    parts = [
        f"用户需求: {user_input}",
        f"可用工具箱:\n{json.dumps(toolbox_data, ensure_ascii=False)}",
    ]
    tool_hint = _format_toolbox_tool_names(registry, [toolbox.id for toolbox in toolboxes])
    if tool_hint:
        parts.append(
            "各工具箱内可用工具名称（规划时请对齐 requiredToolboxes 与工具箱 id）:\n" + tool_hint
        )
    knowledge = retrieve_knowledge_context(
        knowledge_registry,
        user_input,
        phase="planner",
        default_top_k=2,
        default_max_chars=2000,
    )
    if knowledge:
        parts.append(knowledge)
    completed = _completed_work_context(agent_config)
    if completed:
        parts.append(completed)
    return [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _parse_planner_completion(
    response: LLMCompletion,
    *,
    default_step_thinking: str,
) -> tuple[StructuredPlan | None, _PlannerAttemptFailure | None]:
    """解析规划响应并验证执行器依赖的最小合同。"""
    if not response.content:
        return None, _empty_response_failure(response)
    try:
        data = parse_llm_json_response(response.content)
    except (json.JSONDecodeError, TypeError):
        return None, _PlannerAttemptFailure("invalid_json")
    if "steps" not in data or "requiredToolboxes" not in data:
        return None, _PlannerAttemptFailure("invalid_plan_contract")
    try:
        return _dict_to_plan(data, default_step_thinking=default_step_thinking), None
    except (KeyError, TypeError, ValueError):
        return None, _PlannerAttemptFailure("invalid_plan_contract")


# ─── 常量 ───────────────────────────────────────────────

# PLAN_SYSTEM_PROMPT 现在从 miniagent.core.prompts.planner 导入
# 使用 XML 标签结构化，遵循 Claude 最佳实践

# ─── 公共 API ───────────────────────────────────────────


@dataclass(slots=True)
class _PlannerRunner:
    """执行规划请求序列并维护协议降级、恢复参数和追踪关联。"""

    user_input: str
    client: Any
    messages: list[dict[str, str]]
    json_messages: list[dict[str, str]]
    original_params: dict[str, Any]
    params: dict[str, Any]
    session_key: str
    wire_api: WireAPI
    model_max_tokens: int
    default_step_thinking: str
    log_file: str | None
    use_json_object: bool = True
    failures: list[str] | None = None
    call_id: str = ""
    started_ns: int = 0

    def __post_init__(self) -> None:
        """为每次运行创建独立失败历史，避免实例间共享可变状态。"""
        self.failures = []

    def emit_request(self, attempt: int, *, protocol_fallback: bool = False) -> None:
        """记录一次物理请求的安全元数据。"""
        from miniagent.infrastructure.tracing import emit_trace, llm_request_size_metrics

        request_messages = self.json_messages if self.use_json_object else self.messages
        emit_trace(
            {
                "type": "llm.request",
                "call_id": self.call_id,
                "phase": "plan",
                "session_key": self.session_key,
                "attempt": attempt,
                "model": self.params["model"],
                "json_object": self.use_json_object,
                "protocol_fallback": protocol_fallback or None,
                "reasoning_level": self.params.get("_thinking_level"),
                "max_tokens": self.params.get("max_tokens"),
                "sampling_removed": "temperature" not in self.params and "top_p" not in self.params,
                "structured_stream": self.wire_api == "responses" and self.use_json_object,
                "message_count": len(request_messages),
                "tool_count": 0,
                **llm_request_size_metrics(request_messages),
            }
        )

    def emit_response(
        self,
        attempt: int,
        *,
        failure: _PlannerAttemptFailure | None,
        retrying: bool,
        response: LLMCompletion | None = None,
    ) -> None:
        """记录规划响应状态、耗时和可选用量。"""
        from miniagent.infrastructure.tracing import emit_trace

        payload: dict[str, Any] = {
            "type": "llm.response",
            "call_id": self.call_id,
            "phase": "plan",
            "session_key": self.session_key,
            "attempt": attempt,
            "model": self.params["model"],
            "failure_category": failure.category if failure else None,
            "retrying": retrying,
            "duration_ms": (time.monotonic_ns() - self.started_ns) // 1_000_000,
        }
        if failure and failure.status_code is not None:
            payload["status_code"] = failure.status_code
        if response is not None:
            usage = response.usage
            payload.update(
                {
                    "status": response.status,
                    "output_item_types": list(response.output_item_types),
                    "incomplete_reason": response.incomplete_reason,
                    "finish_reason": response.finish_reason,
                    "usage": usage.model_dump()
                    if usage is not None and hasattr(usage, "model_dump")
                    else None,
                }
            )
        emit_trace(payload)

    async def request(self, attempt: int) -> LLMCompletion:
        """执行一次逻辑请求；不支持 JSON Object 时在本轮内降级。"""
        from miniagent.infrastructure.tracing import new_trace_id

        self.call_id = new_trace_id("llm")
        self.started_ns = time.monotonic_ns()
        self.emit_request(attempt)
        safe_agent_debug_log(
            location="planner.py:generate_plan",
            message="before_planner_completion",
            data={
                "attempt": attempt,
                "model": self.params.get("model"),
                "json_object": self.use_json_object,
                "wire_api": self.wire_api,
            },
        )
        try:
            if self.use_json_object:
                return await create_structured_completion(
                    self.client, messages=self.json_messages, params=self.params
                )
            return await create_completion(self.client, messages=self.messages, params=self.params)
        except Exception as error:
            if not self.use_json_object or not _json_object_unsupported(error):
                raise
        self.emit_response(
            attempt,
            failure=_PlannerAttemptFailure("json_object_unsupported"),
            retrying=True,
        )
        self.use_json_object = False
        _logger.info("Planner: API 不支持 response_format json_object，已降级")
        self.call_id = new_trace_id("llm")
        self.started_ns = time.monotonic_ns()
        self.emit_request(attempt, protocol_fallback=True)
        return await create_completion(self.client, messages=self.messages, params=self.params)

    def append_log(self, attempt: int, response: LLMCompletion) -> None:
        """按配置写入截断后的规划请求与响应摘要。"""
        if not self.log_file:
            return
        append_log(
            self.log_file,
            {
                "phase": "plan",
                "attempt": attempt,
                "req": {
                    "model": self.params["model"],
                    "messages": [
                        {"role": message["role"], "content": truncate(message.get("content", ""), 500)}
                        for message in self.messages
                    ],
                },
                "res": {
                    "content": truncate(response.content, 2000),
                    "usage": response.usage.model_dump() if response.usage else None,
                },
            },
        )

    async def prepare_retry(self, failure: _PlannerAttemptFailure, next_attempt: int) -> None:
        """调整下一轮恢复参数，并仅对 Responses 执行退避。"""
        self.params = _planner_retry_params(
            current=self.params,
            failure=failure,
            next_attempt=next_attempt,
            wire_api=self.wire_api,
            model_max_tokens=self.model_max_tokens,
        )
        if self.wire_api == "responses":
            await asyncio.sleep(structured_retry_delay(next_attempt))

    async def run(self) -> StructuredPlan:
        """运行有界规划循环；不可恢复或耗尽重试时返回内置计划。"""
        assert self.failures is not None
        for attempt in range(1, PLANNER_MAX_RETRIES + 1):
            try:
                response = await self.request(attempt)
                plan, failure = _parse_planner_completion(
                    response, default_step_thinking=self.default_step_thinking
                )
                self.emit_response(
                    attempt,
                    failure=failure,
                    retrying=bool(failure and failure.retryable and attempt < PLANNER_MAX_RETRIES),
                    response=response,
                )
                if failure is not None:
                    raise failure
                assert plan is not None
                self.append_log(attempt, response)
                if self.failures:
                    _logger.info(
                        "Planner recovered on attempt %d after %s (reasoning=%s, max_tokens=%s, budget_adjusted=%s)",
                        attempt,
                        ",".join(self.failures),
                        self.params.get("_thinking_level"),
                        self.params.get("max_tokens"),
                        self.params.get("max_tokens") != self.original_params.get("max_tokens"),
                    )
                return plan
            except Exception as error:
                failure = error if isinstance(error, _PlannerAttemptFailure) else _api_failure(error)
                self.failures.append(failure.category)
                if not isinstance(error, _PlannerAttemptFailure):
                    self.emit_response(
                        attempt,
                        failure=failure,
                        retrying=failure.retryable and attempt < PLANNER_MAX_RETRIES,
                    )
                safe_agent_debug_log(
                    location="planner.py:generate_plan",
                    message="planner_attempt_failed",
                    data={
                        "attempt": attempt,
                        "exc_type": type(error).__name__,
                        "failure_category": failure.category,
                        "retryable": failure.retryable,
                        "status_code": failure.status_code,
                    },
                )
                if attempt == PLANNER_MAX_RETRIES or not failure.retryable:
                    _logger.warning(
                        "Planner fallback after %d attempt(s); failures=%s",
                        attempt,
                        ",".join(self.failures),
                    )
                    return _fallback_plan(self.user_input)
                await self.prepare_retry(failure, attempt + 1)
        raise AssertionError("unreachable")  # pragma: no cover


async def generate_plan(
    user_input: str,
    toolboxes: list[Toolbox],
    log_file: str | None = None,
    *,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    agent_config: AgentConfig | None = None,
    registry: Any | None = None,
    planner_model_overrides: dict[str, Any] | None = None,
    default_step_thinking: str = "medium",
) -> StructuredPlan:
    """生成结构化计划；协议或内容最终失败时返回可执行的内置计划。

    工具元数据、知识库和会话历史会进入规划上下文。JSON Object 不受支持时在
    同一逻辑轮次降级，Responses 暂态/空响应最多尝试三次，Chat 尝试两次。
    """
    from miniagent.core.llm_params import resolve_planner_completion_kwargs

    ac: AgentConfig | None = agent_config if isinstance(agent_config, AgentConfig) else None
    planner_kw = resolve_planner_completion_kwargs(ac, merge_overrides=planner_model_overrides)
    plan_session_key = (
        ac.session_config.session_key if ac and ac.session_config.session_key else "default"
    )

    messages = _build_planner_messages(
        user_input,
        toolboxes,
        registry=registry,
        knowledge_registry=knowledge_registry,
        agent_config=ac,
    )
    json_object_messages = ensure_json_object_user_message(messages)

    from miniagent.core.config import get_default_model_config

    model_config = get_default_model_config()
    runner = _PlannerRunner(
        user_input=user_input,
        client=client,
        messages=messages,
        json_messages=json_object_messages,
        original_params=dict(planner_kw),
        params=dict(planner_kw),
        session_key=plan_session_key,
        wire_api=model_config.wire_api,
        model_max_tokens=model_config.max_tokens,
        default_step_thinking=default_step_thinking,
        log_file=log_file,
    )
    return await runner.run()


# ─── 内部辅助 ───────────────────────────────────────────


def _empty_response_failure(response: LLMCompletion) -> _PlannerAttemptFailure:
    return _PlannerAttemptFailure(
        completion_failure_category(response) or "empty_gateway_response",
        incomplete_reason=response.incomplete_reason,
    )


def _api_failure(error: Exception) -> _PlannerAttemptFailure:
    failure = classify_transport_error(error)
    return _PlannerAttemptFailure(
        failure.category,
        retryable=failure.retryable,
        status_code=failure.status_code,
    )


def _planner_retry_params(
    *,
    current: dict[str, Any],
    failure: _PlannerAttemptFailure,
    next_attempt: int,
    wire_api: WireAPI,
    model_max_tokens: int,
) -> dict[str, Any]:
    """Build bounded recovery parameters without changing the first request."""
    if wire_api != "responses":
        return dict(current)
    return structured_retry_params(
        current,
        next_attempt=next_attempt,
        max_attempts=PLANNER_MAX_RETRIES,
        final_reasoning="medium",
        model_max_tokens=model_max_tokens,
        incomplete_reason=failure.incomplete_reason,
    )


@dataclass(slots=True)
class _PlanDictParser:
    """把不可信的规划 JSON 转换为执行器可消费的强类型对象。"""

    data: dict[str, Any]
    default_step_thinking: str

    def mapping(self, key: str) -> dict[str, Any]:
        """读取嵌套对象；模型返回其他类型时使用空对象。"""
        value = self.data.get(key)
        return value if isinstance(value, dict) else {}

    def step_as_dict(self, value: Any, index: int) -> dict[str, Any]:
        """兼容完整步骤对象和只有描述文本的简写步骤。"""
        if isinstance(value, dict):
            return value
        return {
            "stepNumber": index,
            "description": str(value),
            "requiredToolboxes": [],
            "expectedInput": "",
            "expectedOutput": "",
            "dependsOn": None,
            "thinkingLevel": None,
        }

    def fallback_thinking(self) -> ThinkingLevel | None:
        """解析计划级默认思考档位。"""
        raw = str(
            self.data.get("defaultStepThinkingLevel") or self.default_step_thinking
        ).lower()
        return cast(ThinkingLevel, raw) if raw in ("low", "medium", "high") else None

    def step_thinking(self, step: dict[str, Any]) -> ThinkingLevel | None:
        """解析步骤思考档位，非法或缺省值回落到计划默认值。"""
        value = step.get("thinkingLevel")
        if value in (None, ""):
            return self.fallback_thinking()
        normalized = str(value).lower()
        if normalized in ("low", "medium", "high"):
            return cast(ThinkingLevel, normalized)
        return self.fallback_thinking()

    def steps(self) -> list[PlanStep]:
        """解析并规范化顶层步骤。"""
        raw = self.data.get("steps", [])
        raw_steps = raw if isinstance(raw, list) else []
        parsed = parse_plan_steps_from_raw(
            raw_steps,
            step_as_dict=self.step_as_dict,
            step_thinking_level=self.step_thinking,
        )
        return _normalize_plan_steps(parsed)

    def chunks(self, context: dict[str, Any]) -> list[PlanChunk] | None:
        """解析上下文分块，并独立规范化每个分块内的步骤。"""
        raw = context.get("chunks")
        if not isinstance(raw, list):
            return None
        parsed = parse_plan_chunks_from_raw(
            raw,
            step_as_dict=self.step_as_dict,
            step_thinking_level=self.step_thinking,
        )
        if not parsed:
            return None
        return [
            PlanChunk(
                chunk_number=chunk.chunk_number,
                steps=_normalize_plan_steps(chunk.steps),
                estimated_tokens=chunk.estimated_tokens,
                chunk_system_prompt=chunk.chunk_system_prompt,
            )
            for chunk in parsed
        ]

    def context_strategy(self, context: dict[str, Any]) -> ContextStrategy:
        """构造上下文策略并收敛模型可能生成的非法枚举值。"""
        raw_mode = str(context.get("mode", "normal") or "normal").lower()
        mode: ContextMode = (
            cast(ContextMode, raw_mode)
            if raw_mode in ("normal", "chunked", "summarize", "truncate")
            else "normal"
        )
        return ContextStrategy(
            mode=mode,
            chunks=self.chunks(context),
            reason=str(context.get("reason", "") or ""),
        )

    def output_spec(self, output: dict[str, Any]) -> OutputSpec:
        """构造输出规格，未知格式安全回落到 Markdown。"""
        raw_format = str(output.get("format", "markdown") or "markdown").lower()
        output_format: OutputFormat = (
            cast(OutputFormat, raw_format)
            if raw_format in ("text", "markdown", "structured")
            else "markdown"
        )
        return OutputSpec(
            language=output.get("language", "zh-CN"),
            format=output_format,
            expected_deliverable=output.get("expectedDeliverable", ""),
        )

    def build(self) -> StructuredPlan:
        """组装最终计划；所有嵌套对象在此之前已完成类型收敛。"""
        steps = self.steps()
        suggested = self.mapping("suggestedConfig")
        tokens = self.mapping("estimatedTokens")
        context = self.mapping("contextStrategy")
        cost = self.mapping("estimatedCost")
        output = self.mapping("outputSpec")
        fallback = self.mapping("fallbackPlan")
        overrides = suggested.get("modelOverrides")
        return StructuredPlan(
            summary=self.data.get("summary", ""),
            steps=steps,
            required_toolboxes=_dedupe_toolboxes(self.data.get("requiredToolboxes", []), steps),
            suggested_config=SuggestedConfig(
                max_turns=suggested.get("maxTurns"),
                tool_timeout=suggested.get("toolTimeout"),
                risk_level=suggested.get("riskLevel"),
                context_overflow_strategy=suggested.get("contextOverflowStrategy"),
                tool_selection_strategy=suggested.get("toolSelectionStrategy"),
                model_overrides=overrides if isinstance(overrides, dict) else None,
                thinking_level=suggested.get("thinkingLevel"),
                chunk_execution=bool(suggested.get("chunkExecution", False)),
                chunk_token_budget=suggested.get("chunkTokenBudget"),
                parallelism=suggested.get("parallelism"),
            ),
            estimated_tokens=EstimatedTokens(
                prompt_tokens=tokens.get("promptTokens", 500),
                completion_tokens=tokens.get("completionTokens", 500),
                tool_result_tokens=tokens.get("toolResultTokens", 200),
                total=tokens.get("total", 1200),
            ),
            context_strategy=self.context_strategy(context),
            requires_confirmation=self.data.get("requiresConfirmation", False),
            confirmation_message=self.data.get("confirmationMessage"),
            risk_level=self.data.get("riskLevel", "low"),
            estimated_cost=EstimatedCost(
                input_tokens=cost.get("inputTokens", 0),
                output_tokens=cost.get("outputTokens", 0),
                total_usd=cost.get("totalUSD", 0.0),
            ),
            output_spec=self.output_spec(output),
            fallback_plan=FallbackPlan(
                degrade_to_simple=fallback.get("degradeToSimple", True),
                degraded_max_turns=fallback.get("degradedMaxTurns", 5),
            ),
        )


def _dict_to_plan(data: dict[str, Any], *, default_step_thinking: str = "medium") -> StructuredPlan:
    """将不可信的 LLM 字典宽容地解析并规范化为结构化计划。"""
    return _PlanDictParser(data, default_step_thinking).build()


def _normalize_plan_steps(steps: list[PlanStep]) -> list[PlanStep]:
    """规范化计划步骤列表：去重、清理空步骤、修复编号和依赖关系。

    对 LLM 返回的原始步骤列表进行后处理，确保步骤的有效性和一致性：
    1. 移除空步骤（description 和 expected_output 均为空）
    2. 去除重复步骤（基于指纹相似度）
    3. 重新编号（从 1 开始连续）
    4. 修复依赖关系（depends_on 映射到新编号）
    5. 去除工具箱列表中的重复项

    **指纹生成规则**（_step_fingerprint）：
    - 优先提取文件路径（如 "config.py"）
    - 识别动作类型（read/discover/analyze/verify/work）
    - 结合工具箱列表生成唯一标识

    Args:
        steps: 原始步骤列表

    Returns:
        list[PlanStep]: 规范化后的步骤列表

    Note:
        该函数是幂等的，多次调用结果相同。
    """
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
    """合并并去重计划级和步骤级工具箱列表。

    从计划的 requiredToolboxes 和各步骤的 required_toolboxes 中收集所有
    工具箱 ID，去除重复项并保持顺序。

    Args:
        raw_toolboxes: 计划级工具箱列表（可能为非列表类型）
        steps: 步骤列表

    Returns:
        list[str]: 去重后的工具箱 ID 列表

    Note:
        空字符串和 None 值会被过滤掉。
    """
    values: list[str] = []
    if isinstance(raw_toolboxes, list):
        values.extend(str(item) for item in raw_toolboxes if str(item).strip())
    for step in steps:
        values.extend(step.required_toolboxes)
    return _unique_strings(values)


def _unique_strings(values: Any) -> list[str]:
    """从列表中提取非空字符串，去重并保持首次出现顺序。"""
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
    """生成步骤的唯一指纹，用于去重判断。

    指纹由三部分组成：动作类型 | 目标标识 | 工具箱列表
    - 动作类型：read/discover/analyze/verify/work（_action_bucket）
    - 目标标识：优先使用文件路径，否则使用规范化文本前 80 字符
    - 工具箱列表：排序后的工具箱 ID 逗号分隔

    **示例**：
    - "read|config.py|filesystem" — 读取配置文件
    - "analyze|测试覆盖率报告|testing" — 分析测试覆盖率

    Args:
        step: 计划步骤对象

    Returns:
        str: 步骤指纹字符串

    Note:
        指纹相同的步骤会被 _normalize_plan_steps 视为重复并去除。
    """
    text = " ".join([step.description, step.expected_input, step.expected_output]).lower()
    path = _first_path_like(text)
    action = _action_bucket(text)
    toolboxes = ",".join(sorted(step.required_toolboxes))
    if path:
        return f"{action}|{path}|{toolboxes}"
    normalized = re.sub(r"\s+", "", text)
    return f"{action}|{normalized[:80]}|{toolboxes}"


def _first_path_like(text: str) -> str:
    match = re.search(
        r"([a-zA-Z0-9_.\\/-]+\.(?:py|md|txt|json|ya?ml|toml|ini|csv|html|css|js|ts))", text
    )
    if match:
        return match.group(1).replace("\\", "/").lower()
    return ""


def _action_bucket(text: str) -> str:
    """将步骤描述归类为 read/discover/analyze/verify/work，供指纹去重使用。"""
    if any(term in text for term in ("读取", "read", "查看", "打开")):
        return "read"
    if any(term in text for term in ("扫描", "查找", "list", "搜索")):
        return "discover"
    if any(term in text for term in ("分析", "审查", "总结", "解释", "review", "analy")):
        return "analyze"
    if any(term in text for term in ("测试", "验证", "pytest", "compile", "ruff")):
        return "verify"
    return "work"


# ``_normalize_plan_steps`` 对外导出供单测与步骤后处理复用（非公共 API）。
__all__ = ["generate_plan", "_normalize_plan_steps"]
