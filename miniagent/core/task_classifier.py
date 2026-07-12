"""任务难度轻量分类（可选）：在结构化规划前估算 simple/normal/medium/complex。

Internal 常量 ``EXECUTION_TASK_CLASSIFIER_ENABLED``（``constants.py``，默认开启）控制是否启用；
简单任务可跳过 Phase 1 规划并由 ``agent`` 下调 thinking。LLM 分类失败或输出无法识别时降级为
``NORMAL``；与 ``thinking_presets``、``llm_params`` 协同。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
from miniagent.core._openai_compat import (
    ensure_json_object_user_message,
)
from miniagent.core._openai_compat import (
    json_object_unsupported as _json_object_unsupported,
)
from miniagent.core.llm_json import parse_llm_json_response
from miniagent.core.llm_transport import (
    LLMCompletion,
    classify_transport_error,
    completion_failure_category,
    create_completion,
    create_structured_completion,
    resolve_wire_api,
    structured_retry_delay,
    structured_retry_params,
)
from miniagent.core.prompts.classifier import CLASSIFIER_PROMPT
from miniagent.core.thinking_presets import map_thinking_level_to_model
from miniagent.infrastructure.debug_ndjson import safe_agent_debug_log
from miniagent.infrastructure.logger import get_logger
from miniagent.types.config import AgentConfig

_logger = get_logger(__name__)


class TaskDifficulty(str, Enum):
    """任务难度离散档位，与规划/执行 thinking 档位映射共用。"""

    SIMPLE = "simple"
    NORMAL = "normal"
    MEDIUM = "medium"
    COMPLEX = "complex"


# 中文容错：模型可能返回中文描述而非英文枚举值。
_ZH_TO_DIFFICULTY: dict[str, TaskDifficulty] = {
    "简单": TaskDifficulty.SIMPLE,
    "一般": TaskDifficulty.NORMAL,
    "普通": TaskDifficulty.NORMAL,
    "中等": TaskDifficulty.MEDIUM,
    "复杂": TaskDifficulty.COMPLEX,
}


def task_classifier_enabled() -> bool:
    """是否启用规划前难度分类。

    读取 ``miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED``（Internal 常量，
    默认 ``True``）。该开关不可通过 ``config.user.json`` 覆盖，需修改 ``constants.py`` 并重新部署。
    """
    from miniagent.core.constants import EXECUTION_TASK_CLASSIFIER_ENABLED

    return EXECUTION_TASK_CLASSIFIER_ENABLED


def planner_merge_for_difficulty(d: TaskDifficulty) -> dict[str, Any]:
    """规划阶段 ``model_overrides`` 合并片段（``thinking_level`` / ``thinking_budget``）。

    Args:
        d: 任务难度档位。

    Returns:
        含 ``thinking_level`` 与 ``thinking_budget`` 的字典，供 ``generate_plan`` 合并。
    """
    if d == TaskDifficulty.MEDIUM:
        key = "medium"
    elif d == TaskDifficulty.COMPLEX:
        key = "high"
    else:
        # NORMAL 和 SIMPLE 均使用 low
        key = "low"
    tl, tb = map_thinking_level_to_model(key)
    return {"thinking_level": tl, "thinking_budget": tb}


def default_step_thinking_for_difficulty(d: TaskDifficulty) -> str:
    """``generate_plan`` 的 ``default_step_thinking``（步骤缺省 thinkingLevel 时回填）。

    Args:
        d: 任务难度档位。

    Returns:
        业务档位字符串（``low`` / ``medium`` / ``high``）。
    """
    if d in (TaskDifficulty.NORMAL, TaskDifficulty.SIMPLE):
        return "low"
    if d == TaskDifficulty.MEDIUM:
        return "medium"
    return "high"


def exec_merge_for_simple_path() -> dict[str, Any]:
    """跳过规划时执行阶段统一使用 low 档位。

    Returns:
        含 ``thinking_level`` 与 ``thinking_budget`` 的字典，与
        ``planner_merge_for_difficulty(TaskDifficulty.NORMAL)`` 等价。
    """
    tl, tb = map_thinking_level_to_model("low")
    return {"thinking_level": tl, "thinking_budget": tb}


def _log_classifier_fallback(reason: str, **data: Any) -> None:
    safe_agent_debug_log(
        location="task_classifier.py:classify_task_difficulty",
        message=reason,
        data=data,
    )


def _parse_difficulty(response: LLMCompletion) -> tuple[TaskDifficulty | None, str | None]:
    """解析分类响应，返回难度及规范化失败分类。"""
    raw = (response.content or "").strip()
    failure_category = completion_failure_category(response)
    if not raw:
        return None, failure_category
    try:
        data = parse_llm_json_response(raw)
        value = str(data.get("difficulty", "")).strip().lower()
    except (ValueError, TypeError):
        return None, "invalid_json"
    difficulty = next(
        (item for item in TaskDifficulty if item.value == value),
        _ZH_TO_DIFFICULTY.get(value),
    )
    return difficulty, failure_category if difficulty is not None else "invalid_classifier_contract"


@dataclass(slots=True)
class _ClassifierRunner:
    """持有一次任务分类调用序列的协议状态与恢复参数。"""

    client: Any
    messages: list[dict[str, str]]
    json_messages: list[dict[str, str]]
    params: dict[str, Any]
    session_key: str
    responses_wire: bool
    model_max_tokens: int
    use_json_object: bool = True
    failures: list[str] = field(default_factory=list)

    @property
    def max_attempts(self) -> int:
        """返回当前传输协议允许的最大尝试次数。"""
        return 3 if self.responses_wire else 2

    def emit_request(self, call_id: str, attempt: int) -> None:
        """记录不含正文的分类请求元数据。"""
        from miniagent.infrastructure.tracing import emit_trace, llm_request_size_metrics

        request_messages = self.json_messages if self.use_json_object else self.messages
        emit_trace(
            {
                "type": "llm.request",
                "call_id": call_id,
                "phase": "classify",
                "session_key": self.session_key,
                "attempt": attempt,
                "model": self.params.get("model"),
                "json_object": self.use_json_object,
                "structured_stream": self.responses_wire and self.use_json_object,
                "message_count": len(request_messages),
                "tool_count": 0,
                **llm_request_size_metrics(request_messages),
            }
        )

    async def request(self) -> LLMCompletion:
        """按当前格式开关执行一次物理请求。"""
        if self.use_json_object:
            return await create_structured_completion(
                self.client,
                messages=self.json_messages,
                params=self.params,
            )
        return await create_completion(
            self.client,
            messages=self.messages,
            params=self.params,
        )

    def emit_response(
        self,
        call_id: str,
        attempt: int,
        started_ns: int,
        *,
        failure_category: str | None,
        retrying: bool,
        response: LLMCompletion | None = None,
        status_code: int | None = None,
    ) -> None:
        """记录请求结果；仅包含状态、耗时和计量信息。"""
        from miniagent.infrastructure.tracing import emit_trace

        payload: dict[str, Any] = {
            "type": "llm.response",
            "call_id": call_id,
            "phase": "classify",
            "session_key": self.session_key,
            "attempt": attempt,
            "model": self.params.get("model"),
            "failure_category": failure_category,
            "retrying": retrying,
            "duration_ms": (time.monotonic_ns() - started_ns) // 1_000_000,
        }
        if status_code is not None:
            payload["status_code"] = status_code
        if response is not None:
            usage = response.usage
            payload.update(
                {
                    "status": response.status,
                    "output_item_types": list(response.output_item_types),
                    "incomplete_reason": response.incomplete_reason,
                    "usage": usage.model_dump()
                    if usage is not None and hasattr(usage, "model_dump")
                    else None,
                }
            )
        emit_trace(payload)

    async def recover(self, next_attempt: int, *, incomplete_reason: str | None = None) -> None:
        """为 Responses 下一轮调整预算并执行有界退避。"""
        if not self.responses_wire:
            return
        self.params = structured_retry_params(
            self.params,
            next_attempt=next_attempt,
            max_attempts=self.max_attempts,
            final_reasoning="low",
            model_max_tokens=self.model_max_tokens,
            incomplete_reason=incomplete_reason,
        )
        await asyncio.sleep(structured_retry_delay(next_attempt))

    async def handle_api_error(
        self,
        error: Exception,
        *,
        call_id: str,
        attempt: int,
        started_ns: int,
    ) -> TaskDifficulty | None:
        """处理协议降级、暂态恢复和最终 API 降级。"""
        if self.use_json_object and _json_object_unsupported(error):
            self.use_json_object = False
            _logger.info("任务分类: API 不支持 json_object，已降级为普通 JSON 输出")
            self.emit_response(
                call_id,
                attempt,
                started_ns,
                failure_category="json_object_unsupported",
                retrying=True,
            )
            return None
        failure = classify_transport_error(error)
        self.failures.append(failure.category)
        retrying = self.responses_wire and failure.retryable and attempt < self.max_attempts
        self.emit_response(
            call_id,
            attempt,
            started_ns,
            failure_category=failure.category,
            retrying=retrying,
            status_code=failure.status_code,
        )
        if retrying:
            _logger.info("任务难度分类第 %d 次遇到可恢复的 %s，准备重试", attempt, failure.category)
            await self.recover(attempt + 1)
            return None
        _log_classifier_fallback(
            "classifier_failed",
            exc_type=type(error).__name__,
            failure_category=failure.category,
        )
        _logger.warning("任务难度分类最终失败，降级为 normal: category=%s", failure.category)
        return TaskDifficulty.NORMAL

    async def handle_response(
        self,
        response: LLMCompletion,
        *,
        call_id: str,
        attempt: int,
        started_ns: int,
    ) -> TaskDifficulty | None:
        """解析一次响应，并在需要时准备下一轮参数。"""
        difficulty, failure_category = _parse_difficulty(response)
        retrying = (
            difficulty is None
            and attempt < self.max_attempts
            and not (
                not self.responses_wire and failure_category == "invalid_classifier_contract"
            )
        )
        self.emit_response(
            call_id,
            attempt,
            started_ns,
            failure_category=failure_category,
            retrying=bool(failure_category) and retrying,
            response=response,
        )
        if difficulty is not None:
            return difficulty
        if not self.responses_wire and failure_category == "invalid_classifier_contract":
            _log_classifier_fallback("classifier_unknown_difficulty")
            return TaskDifficulty.NORMAL
        self.failures.append(failure_category or "invalid_json")
        if attempt < self.max_attempts:
            _logger.info(
                "任务难度分类第 %d 次返回 %s，准备重试",
                attempt,
                failure_category or "invalid_json",
            )
            await self.recover(attempt + 1, incomplete_reason=response.incomplete_reason)
            return None
        _log_classifier_fallback(
            "classifier_no_response",
            failure_categories=tuple(self.failures),
        )
        _logger.warning("任务难度分类最终失败，降级为 normal: failures=%s", ",".join(self.failures))
        return TaskDifficulty.NORMAL

    async def run(self) -> TaskDifficulty:
        """执行有界分类循环，任何最终失败均安全降级为 NORMAL。"""
        from miniagent.infrastructure.tracing import new_trace_id

        for attempt in range(1, self.max_attempts + 1):
            call_id = new_trace_id("llm")
            started_ns = time.monotonic_ns()
            safe_agent_debug_log(
                location="task_classifier.py:classify_task_difficulty",
                message="before_structured_completion",
                data={
                    "attempt": attempt,
                    "model": self.params.get("model"),
                    "json_object": self.use_json_object,
                    "structured_stream": self.responses_wire and self.use_json_object,
                },
            )
            self.emit_request(call_id, attempt)
            try:
                response = await self.request()
            except Exception as error:
                result = await self.handle_api_error(
                    error, call_id=call_id, attempt=attempt, started_ns=started_ns
                )
            else:
                result = await self.handle_response(
                    response, call_id=call_id, attempt=attempt, started_ns=started_ns
                )
            if result is not None:
                return result
        return TaskDifficulty.NORMAL


async def classify_task_difficulty(
    user_input: str,
    toolbox_ids: list[str],
    *,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    agent_config: AgentConfig | None = None,
) -> TaskDifficulty:
    """使用低开销 LLM 调用估算任务难度，失败时降级为 NORMAL。

    根据用户输入复杂度和可用工具箱，判断任务属于 simple/normal/medium/complex 四档。
    简单任务可跳过规划阶段并降低 thinking 档位，以减少延迟和成本。

    Args:
        user_input: 用户原始输入文本
        toolbox_ids: 可用工具箱 ID 列表（用于复杂度判断）
        knowledge_registry: 由组合根注入的知识库注册表
        client: 由组合根显式注入的 LLM 客户端
        agent_config: Agent 配置（可选，用于参数覆盖）

    Returns:
        TaskDifficulty: 任务难度枚举值
        - SIMPLE: 单步可答，无需工具或极简单查询
        - NORMAL: 常规多步但清晰，默认档位
        - MEDIUM: 需多工具协作或中等推理
        - COMPLEX: 长链路、强依赖工具或高风险

    Note:
        - 是否启用由 Internal 常量 ``EXECUTION_TASK_CLASSIFIER_ENABLED`` 控制（见
          :func:`task_classifier_enabled`）
        - 使用 planner 级参数（低温度、小 max_tokens）降低成本
        - Responses 结构化流最多尝试三次；Chat 保持原两次尝试
        - 最终空文本、JSON 解析失败或 ``difficulty`` 无法识别时返回 NORMAL
        - HTTP 超时沿用 ``agent.http_timeout`` 等全局客户端配置

    RAG 增强：分类阶段会检索知识库（可选），辅助判断任务难度。
        若知识库有直接答案，建议分类为 simple。受 ``knowledge.classifier_*`` 配置控制。
    """
    from miniagent.core.llm_params import resolve_planner_completion_kwargs
    from miniagent.knowledge import retrieve_knowledge_context

    classify_session_key = (
        agent_config.session_config.session_key
        if agent_config and agent_config.session_config.session_key
        else "default"
    )

    # ── RAG 增强：知识库检索（使用公共函数）──
    kb_hint = retrieve_knowledge_context(
        knowledge_registry,
        user_input,
        phase="classifier",
        default_top_k=2,
        default_max_chars=1500,
    )

    # 使用优化后的分类器提示词（XML 结构化，包含示例）
    sys_prompt = CLASSIFIER_PROMPT
    tb_line = ", ".join(toolbox_ids[:32]) if toolbox_ids else "(无)"
    user_msg = f"用户诉求:\n{user_input}\n\n工具箱 id: {tb_line}"
    # 注入知识库检索结果
    if kb_hint:
        user_msg += kb_hint

    kw = resolve_planner_completion_kwargs(
        agent_config,
        merge_overrides={
            "planner_max_tokens": 128,
            "planner_temperature": 0.0,
            "thinking_level": "disabled",
            "thinking_budget": 0,
        },
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ]
    json_object_messages = ensure_json_object_user_message(messages)
    from miniagent.core.config import get_default_model_config

    model_config = get_default_model_config()
    responses_wire = resolve_wire_api() == "responses"
    runner = _ClassifierRunner(
        client=client,
        messages=messages,
        json_messages=json_object_messages,
        params=dict(kw),
        session_key=classify_session_key,
        responses_wire=responses_wire,
        model_max_tokens=model_config.max_tokens,
    )
    return await runner.run()


__all__ = [
    "TaskDifficulty",
    "task_classifier_enabled",
    "classify_task_difficulty",
    "planner_merge_for_difficulty",
    "default_step_thinking_for_difficulty",
    "exec_merge_for_simple_path",
]
