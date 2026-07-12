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


# ─── 常量 ───────────────────────────────────────────────

# PLAN_SYSTEM_PROMPT 现在从 miniagent.core.prompts.planner 导入
# 使用 XML 标签结构化，遵循 Claude 最佳实践

# ─── 公共 API ───────────────────────────────────────────


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
    """根据用户需求和可用工具箱生成结构化执行计划。

    调用 LLM 返回单一 JSON 对象，经 :func:`_dict_to_plan` 解析为
    :class:`~miniagent.types.planning.StructuredPlan`。解析失败或网络错误时
    最多重试 :data:`~miniagent.core.constants.PLANNER_MAX_RETRIES` 次；全部失败则
    返回 :func:`_fallback_plan` 兜底计划，保证 Phase 2 始终有可消费结构。

    **上下文增强**（注入 user 消息）：
    - 可用工具箱元数据（id / name / description / keywords）
    - ``registry`` 提供的各工具箱内工具名称映射
    - RAG 知识库检索结果（:func:`miniagent.knowledge.retrieve_knowledge_context`）
    - 对话历史中已完成的读取/分析/测试等工作摘要

    **JSON 模式**：优先 ``response_format={"type": "json_object"}``；若 API
    不支持则当次请求内降级为普通 completion（不重计 attempt）。

    Args:
        user_input: 用户原始需求文本。
        toolboxes: 当前可用的工具箱列表。
        log_file: 可选日志文件路径；非空时将请求/响应摘要写入 NDJSON 日志。
        client: 由组合根注入的 AsyncOpenAI 兼容客户端。
        agent_config: 可选 :class:`~miniagent.types.config.AgentConfig`；
            通过 ``session_config`` 提供 session_key、conversation_history 等规划上下文。
        registry: 可选工具注册表（需实现 ``get_all()``），用于生成工具箱→工具名映射。
        knowledge_registry: 由组合根注入的知识库注册表。
        planner_model_overrides: 规划阶段 LLM 参数覆盖，与
            :func:`~miniagent.core.llm_params.resolve_planner_completion_kwargs` 合并。
        default_step_thinking: LLM 未指定 ``thinkingLevel`` 时的默认档位
           （``low`` / ``medium`` / ``high``）。

    Returns:
        StructuredPlan: 结构化执行计划；失败时为单步 fallback 计划。

    See Also:
        - :func:`miniagent.core.agent.run_agent` — Phase 1 编排入口
        - :mod:`miniagent.core.executor` — Phase 2 消费方
    """
    from miniagent.core.llm_params import resolve_planner_completion_kwargs
    from miniagent.infrastructure.tracing import emit_trace, llm_request_size_metrics, new_trace_id
    from miniagent.knowledge import retrieve_knowledge_context

    ac: AgentConfig | None = agent_config if isinstance(agent_config, AgentConfig) else None
    planner_kw = resolve_planner_completion_kwargs(ac, merge_overrides=planner_model_overrides)
    plan_session_key = (
        ac.session_config.session_key if ac and ac.session_config.session_key else "default"
    )

    # ── RAG 增强：知识库检索（使用公共函数）──
    kb_context_planner = retrieve_knowledge_context(
        knowledge_registry,
        user_input,
        phase="planner",
        default_top_k=2,
        default_max_chars=2000,
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
    json_object_messages = ensure_json_object_user_message(messages)

    llm_client = client
    use_json_object = True
    from miniagent.core.config import get_default_model_config

    model_config = get_default_model_config()
    wire_api = model_config.wire_api
    attempt_kw = dict(planner_kw)
    failure_history: list[str] = []

    for attempt in range(PLANNER_MAX_RETRIES):
        call_id = new_trace_id("llm")
        attempt_start_ns = time.monotonic_ns()
        try:
            use_structured_stream = wire_api == "responses" and use_json_object
            emit_trace(
                {
                    "type": "llm.request",
                    "call_id": call_id,
                    "phase": "plan",
                    "session_key": plan_session_key,
                    "attempt": attempt + 1,
                    "model": attempt_kw["model"],
                    "json_object": use_json_object,
                    "reasoning_level": attempt_kw.get("_thinking_level"),
                    "max_tokens": attempt_kw.get("max_tokens"),
                    "sampling_removed": (
                        "temperature" not in attempt_kw and "top_p" not in attempt_kw
                    ),
                    "structured_stream": use_structured_stream,
                    "message_count": len(json_object_messages if use_json_object else messages),
                    "tool_count": 0,
                    **llm_request_size_metrics(
                        json_object_messages if use_json_object else messages
                    ),
                }
            )
            safe_agent_debug_log(
                location="planner.py:generate_plan",
                message="before_planner_completion",
                data={
                    "attempt": attempt + 1,
                    "model": attempt_kw.get("model"),
                    "json_object": use_json_object,
                    "wire_api": wire_api,
                },
            )
            try:
                request_messages = json_object_messages if use_json_object else messages
                if use_json_object:
                    response = await create_structured_completion(
                        llm_client,
                        messages=request_messages,
                        params=attempt_kw,
                    )
                else:
                    response = await create_completion(
                        llm_client,
                        messages=request_messages,
                        params=attempt_kw,
                    )
            except Exception as api_err:
                if use_json_object and _json_object_unsupported(api_err):
                    use_json_object = False
                    _logger.info("Planner: API 不支持 response_format json_object，已降级")
                    emit_trace(
                        {
                            "type": "llm.response",
                            "call_id": call_id,
                            "phase": "plan",
                            "session_key": plan_session_key,
                            "attempt": attempt + 1,
                            "model": attempt_kw["model"],
                            "failure_category": "json_object_unsupported",
                            "retrying": True,
                            "duration_ms": (time.monotonic_ns() - attempt_start_ns) // 1_000_000,
                        }
                    )
                    call_id = new_trace_id("llm")
                    emit_trace(
                        {
                            "type": "llm.request",
                            "call_id": call_id,
                            "phase": "plan",
                            "session_key": plan_session_key,
                            "attempt": attempt + 1,
                            "model": attempt_kw["model"],
                            "json_object": False,
                            "protocol_fallback": True,
                            "message_count": len(messages),
                            "tool_count": 0,
                            **llm_request_size_metrics(messages),
                        }
                    )
                    attempt_start_ns = time.monotonic_ns()
                    response = await create_completion(
                        llm_client,
                        messages=messages,
                        params=attempt_kw,
                    )
                else:
                    raise

            content = response.content
            failure: _PlannerAttemptFailure | None = None
            plan_data: dict[str, Any] | None = None
            plan: StructuredPlan | None = None
            if not content:
                failure = _empty_response_failure(response)
            else:
                try:
                    plan_data = parse_llm_json_response(content)
                except Exception:
                    failure = _PlannerAttemptFailure("invalid_json")
                if plan_data is not None and (
                    "steps" not in plan_data or "requiredToolboxes" not in plan_data
                ):
                    failure = _PlannerAttemptFailure("invalid_plan_contract")
                elif plan_data is not None:
                    try:
                        plan = _dict_to_plan(
                            plan_data,
                            default_step_thinking=default_step_thinking,
                        )
                    except Exception:
                        failure = _PlannerAttemptFailure("invalid_plan_contract")

            _plan_usage = response.usage
            emit_trace(
                {
                    "type": "llm.response",
                    "call_id": call_id,
                    "phase": "plan",
                    "session_key": plan_session_key,
                    "attempt": attempt + 1,
                    "model": attempt_kw["model"],
                    "status": response.status,
                    "output_item_types": list(response.output_item_types),
                    "incomplete_reason": response.incomplete_reason,
                    "finish_reason": response.finish_reason,
                    "failure_category": failure.category if failure else None,
                    "retrying": bool(
                        failure is not None
                        and failure.retryable
                        and attempt < PLANNER_MAX_RETRIES - 1
                    ),
                    "duration_ms": (time.monotonic_ns() - attempt_start_ns) // 1_000_000,
                    "usage": _plan_usage.model_dump()
                    if _plan_usage is not None and hasattr(_plan_usage, "model_dump")
                    else None,
                }
            )

            if failure is not None:
                raise failure

            if log_file:
                append_log(
                    log_file,
                    {
                        "phase": "plan",
                        "attempt": attempt + 1,
                        "req": {
                            "model": attempt_kw["model"],
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

            assert plan is not None
            if failure_history:
                _logger.info(
                    "Planner recovered on attempt %d after %s "
                    "(reasoning=%s, max_tokens=%s, budget_adjusted=%s)",
                    attempt + 1,
                    ",".join(failure_history),
                    attempt_kw.get("_thinking_level"),
                    attempt_kw.get("max_tokens"),
                    attempt_kw.get("max_tokens") != planner_kw.get("max_tokens"),
                )
            return plan

        except Exception as e:
            planner_failure = e if isinstance(e, _PlannerAttemptFailure) else _api_failure(e)
            failure_history.append(planner_failure.category)
            if not isinstance(e, _PlannerAttemptFailure):
                will_retry = planner_failure.retryable and attempt < PLANNER_MAX_RETRIES - 1
                emit_trace(
                    {
                        "type": "llm.response",
                        "call_id": call_id,
                        "phase": "plan",
                        "session_key": plan_session_key,
                        "attempt": attempt + 1,
                        "model": attempt_kw["model"],
                        "status_code": planner_failure.status_code,
                        "failure_category": planner_failure.category,
                        "retrying": will_retry,
                        "duration_ms": (time.monotonic_ns() - attempt_start_ns) // 1_000_000,
                    }
                )
            safe_agent_debug_log(
                location="planner.py:generate_plan",
                message="planner_attempt_failed",
                data={
                    "attempt": attempt + 1,
                    "exc_type": type(e).__name__,
                    "failure_category": planner_failure.category,
                    "retryable": planner_failure.retryable,
                    "status_code": planner_failure.status_code,
                },
            )
            is_last = attempt == PLANNER_MAX_RETRIES - 1
            if is_last or not planner_failure.retryable:
                _logger.warning(
                    "Planner fallback after %d attempt(s); failures=%s",
                    attempt + 1,
                    ",".join(failure_history),
                )
                return _fallback_plan(user_input)
            if attempt == 0:
                _logger.info(
                    "Planner attempt 1 produced retryable %s; retrying",
                    planner_failure.category,
                )
            else:
                _logger.warning(
                    "Planner attempt %d failed with %s; final recovery retry follows",
                    attempt + 1,
                    planner_failure.category,
                )
            attempt_kw = _planner_retry_params(
                current=attempt_kw,
                failure=planner_failure,
                next_attempt=attempt + 2,
                wire_api=wire_api,
                model_max_tokens=model_config.max_tokens,
            )
            if wire_api == "responses":
                await asyncio.sleep(structured_retry_delay(attempt + 2))

    # 不可达：循环内最后一轮必定返回
    assert False, "unreachable"


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


def _format_toolbox_tool_names(registry: Any, toolbox_ids: list[str]) -> str:
    """按工具箱 ID 列出注册表中的工具名称映射。

    生成工具箱到工具名称的映射文本，用于规划器上下文。帮助 LLM 规划器
    了解每个工具箱包含哪些具体工具，从而在 requiredToolboxes 中做出准确选择。

    **输出格式**：
    ```
    __core__（无工具箱绑定的核心工具）: read_file, write_file, exec_command
    filesystem: list_dir, watch_file
    web: web_search, fetch_url
    ```

    Args:
        registry: 工具注册表实例（需实现 get_all 方法）
        toolbox_ids: 可用工具箱 ID 列表

    Returns:
        str: 工具箱到工具名称的映射文本，无注册表或为空则返回空串

    Note:
        核心工具（toolbox=None）会被单独列为 __core__ 组。
    """
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


def _completed_work_context(agent_config: AgentConfig | None) -> str:
    """从对话历史中提取已完成工作的摘要，供规划器复用。

    扫描最近 20 条对话历史消息，识别包含关键工作标记的条目（如"已读取"、
    "分析"、"测试"、"已完成"等），生成简洁摘要。帮助规划器避免重复步骤，
    直接利用已有结果。

    **识别的关键词**：
    - 文件操作：read_file、已读取
    - 分析工作：分析、review、解释
    - 测试验证：测试、pytest、已完成
    - 知识检索：rag、知识库

    Args:
        agent_config: Agent 配置对象；历史来自 ``session_config.conversation_history``。

    Returns:
        str: 已完成工作摘要文本（含标题），无相关历史则返回空串

    Note:
        最多返回最近 8 条相关记录，每条截断至 180 字符。
    """
    history = agent_config.session_config.conversation_history if agent_config is not None else None
    if not history:
        return ""
    lines: list[str] = []
    for msg in history[-20:]:
        content = str(msg.get("content", "")) if isinstance(msg, dict) else ""
        if not content:
            continue
        low = content.lower()
        if any(
            term in low
            for term in ("read_file", "已读取", "分析", "测试", "pytest", "已完成", "rag", "知识库")
        ):
            lines.append(f"- {content[:180]}")
    if not lines:
        return ""
    return "## 最近已完成工作（规划时应复用，避免重复步骤）\n" + "\n".join(lines[-8:])


def _dict_to_plan(data: dict[str, Any], *, default_step_thinking: str = "medium") -> StructuredPlan:
    """将 LLM 返回的 dict 转为 StructuredPlan。

    解析流程：
    1. 步骤列表（``steps``）→ ``list[PlanStep]``，支持 dict / str / 其它三种输入格式
    2. 嵌套配置（``suggestedConfig`` 等）→ 各字段安全提取，空值回退默认
    3. 经 :func:`_normalize_plan_steps` 去重、重编号、修复依赖
    4. 组装为 :class:`~miniagent.types.planning.StructuredPlan`

    **静默回退**（不抛异常）：
    - ``contextStrategy.mode`` 非法值 → ``"normal"``
    - ``outputSpec.format`` 非法值 → ``"markdown"``
    - ``steps`` 非 list → 空列表
    - 嵌套 dict 字段类型错误 → 对应 dataclass 默认值
    - 非法 ``thinkingLevel`` 保留原字符串，由执行阶段
      :func:`~miniagent.core.thinking_presets.map_business_depth` 再映射

    Args:
        data: LLM 解析后的 JSON dict。
        default_step_thinking: 步骤级 ``thinkingLevel`` 缺省时的回填档位。

    Returns:
        StructuredPlan: 规范化后的结构化计划。
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

    steps = _normalize_plan_steps(
        parse_plan_steps_from_raw(
            raw_steps,
            step_as_dict=_step_as_dict,
            step_thinking_level=_step_thinking_level,
        )
    )

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

    raw_chunks = cs.get("chunks")
    parsed_chunks: list[PlanChunk] | None = None
    if isinstance(raw_chunks, list):
        parsed_chunks = parse_plan_chunks_from_raw(
            raw_chunks,
            step_as_dict=_step_as_dict,
            step_thinking_level=_step_thinking_level,
        )
        if parsed_chunks:
            parsed_chunks = [
                PlanChunk(
                    chunk_number=ch.chunk_number,
                    steps=_normalize_plan_steps(ch.steps),
                    estimated_tokens=ch.estimated_tokens,
                    chunk_system_prompt=ch.chunk_system_prompt,
                )
                for ch in parsed_chunks
            ]

    ctx_mode_raw = str(cs.get("mode", "normal") or "normal").lower()
    ctx_mode: ContextMode = (
        cast(ContextMode, ctx_mode_raw)
        if ctx_mode_raw in ("normal", "chunked", "summarize", "truncate")
        else "normal"
    )

    out_fmt_raw = str(osp.get("format", "markdown") or "markdown").lower()
    out_fmt: OutputFormat = (
        cast(OutputFormat, out_fmt_raw)
        if out_fmt_raw in ("text", "markdown", "structured")
        else "markdown"
    )

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
            mode=ctx_mode,
            chunks=parsed_chunks,
            reason=str(cs.get("reason", "") or ""),
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
            format=out_fmt,
            expected_deliverable=osp.get("expectedDeliverable", ""),
        ),
        fallback_plan=FallbackPlan(
            degrade_to_simple=fb.get("degradeToSimple", True),
            degraded_max_turns=fb.get("degradedMaxTurns", 5),
        ),
    )


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


def _fallback_plan(user_input: str) -> StructuredPlan:
    """生成回退计划：当规划器调用全部失败时的兜底方案。

    在以下场景触发：
    - LLM 规划器连续失败 PLANNER_MAX_RETRIES 次（网络错误、解析错误等）
    - 规划器返回无效 JSON（缺少 steps/requiredToolboxes 字段）

    **回退策略**：
    - 单步直接执行（无详细规划）
    - 低风险等级（risk_level="low"）
    - 较短轮数限制（max_turns=5）
    - 低思考深度（thinking_level="low"）

    Args:
        user_input: 用户原始需求

    Returns:
        StructuredPlan: 回退计划对象

    Note:
        回退计划确保系统在规划器故障时仍能继续执行，避免完全失败。
    """
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


# ``_normalize_plan_steps`` 对外导出供单测与步骤后处理复用（非公共 API）。
__all__ = ["generate_plan", "_normalize_plan_steps"]
