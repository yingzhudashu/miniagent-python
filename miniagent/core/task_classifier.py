"""任务难度轻量分类（可选）：在结构化规划前估算 simple/normal/medium/complex。

配置 execution.task_classifier_enabled 默认开启；简单任务可跳过 Phase 1 规划并由 ``agent`` 下调 thinking。
失败时回落启发式或 JSON 解析兜底；与 ``thinking_presets``、``llm_params`` 协同。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from miniagent.core._openai_compat import (
    ensure_json_object_user_message,
    json_object_unsupported as _json_object_unsupported,
)
from miniagent.core.llm_json import parse_llm_json_response
from miniagent.core.openai_client import get_shared_async_openai
from miniagent.core.prompts.classifier import CLASSIFIER_PROMPT
from miniagent.core.thinking_presets import map_business_depth, map_thinking_level_to_model
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


def task_classifier_enabled() -> bool:
    """是否启用规划前难度分类（默认开启）。"""
    from miniagent.core.constants import EXECUTION_TASK_CLASSIFIER_ENABLED

    return EXECUTION_TASK_CLASSIFIER_ENABLED


def planner_merge_for_difficulty(d: TaskDifficulty) -> dict[str, Any]:
    """规划阶段 model_overrides 合并片段（thinking_level / thinking_budget）。"""
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
    """generate_plan 的 default_step_thinking（步骤缺省 thinkingLevel 时回填）。"""
    if d in (TaskDifficulty.NORMAL, TaskDifficulty.SIMPLE):
        return "low"
    if d == TaskDifficulty.MEDIUM:
        return "medium"
    return "high"


def exec_merge_for_simple_path() -> dict[str, Any]:
    """跳过规划时执行阶段统一使用 low 档位。"""
    tl, tb = map_business_depth("low")
    return {"thinking_level": tl, "thinking_budget": tb}


async def classify_task_difficulty(
    user_input: str,
    toolbox_ids: list[str],
    *,
    client: Any | None = None,
    agent_config: AgentConfig | None = None,
) -> TaskDifficulty:
    """使用低开销 LLM 调用估算任务难度，失败时降级为 NORMAL。

    根据用户输入复杂度和可用工具箱，判断任务属于 simple/normal/medium/complex 四档。
    简单任务可跳过规划阶段并降低 thinking 档位，以减少延迟和成本。

    Args:
        user_input: 用户原始输入文本
        toolbox_ids: 可用工具箱 ID 列表（用于复杂度判断）
        client: LLM 客户端（可选，默认使用共享实例）
        agent_config: Agent 配置（可选，用于参数覆盖）

    Returns:
        TaskDifficulty: 任务难度枚举值
        - SIMPLE: 单步可答，无需工具或极简单查询
        - NORMAL: 常规多步但清晰，默认档位
        - MEDIUM: 需多工具协作或中等推理
        - COMPLEX: 长链路、强依赖工具或高风险

    Note:
        - 配置项 execution.task_classifier_enabled 控制是否启用
        - 使用 planner 级参数（低温度、小 max_tokens）降低成本
        - 解析失败或超时时默认返回 NORMAL

    RAG 增强：分类阶段会检索知识库（可选），辅助判断任务难度。
        若知识库有直接答案，建议分类为 simple。
    """
    from miniagent.core.llm_params import resolve_planner_completion_kwargs
    from miniagent.infrastructure.tracing import emit_trace
    from miniagent.knowledge import retrieve_knowledge_context

    classify_session_key = (
        agent_config.session_key if agent_config and agent_config.session_key else "default"
    )

    # ── RAG 增强：知识库检索（使用公共函数）──
    kb_hint = retrieve_knowledge_context(
        user_input, phase="classifier", default_top_k=2, default_max_chars=1500
    )

    # 使用优化后的分类器提示词（XML 结构化，包含示例）
    sys_prompt = CLASSIFIER_PROMPT
    tb_line = ", ".join(toolbox_ids[:32]) if toolbox_ids else "(无)"
    user_msg = f"用户诉求:\n{user_input}\n\n工具箱 id: {tb_line}"
    # 注入知识库检索结果
    if kb_hint:
        user_msg += kb_hint

    llm = client if client is not None else get_shared_async_openai()
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
    use_json_object = True
    resp = None
    try:
        for _attempt in range(2):
            try:
                create_args: dict[str, Any] = {
                    **kw,
                    "messages": json_object_messages if use_json_object else messages,
                }  # type: ignore[typeddict-item]
                if use_json_object:
                    create_args["response_format"] = {"type": "json_object"}
                safe_agent_debug_log(
                    location="task_classifier.py:classify_task_difficulty",
                    message="before_chat_completions",
                    data={
                        "attempt": _attempt,
                        "model": kw.get("model"),
                        "json_object": use_json_object,
                    },
                )
                emit_trace(
                    {
                        "type": "llm.request",
                        "phase": "classify",
                        "session_key": classify_session_key,
                        "attempt": _attempt + 1,
                        "model": kw.get("model"),
                        "json_object": use_json_object,
                    }
                )
                resp = await llm.chat.completions.create(**create_args)
                _resp_usage = getattr(resp, "usage", None)
                emit_trace(
                    {
                        "type": "llm.response",
                        "phase": "classify",
                        "session_key": classify_session_key,
                        "attempt": _attempt + 1,
                        "model": kw.get("model"),
                        "usage": _resp_usage.model_dump()
                        if _resp_usage is not None and hasattr(_resp_usage, "model_dump")
                        else None,
                    }
                )
                break
            except Exception as api_err:
                if use_json_object and _json_object_unsupported(api_err):
                    use_json_object = False
                    _logger.info("任务分类: API 不支持 json_object，已降级为普通 JSON 输出")
                    continue
                raise
        if resp is None:
            return TaskDifficulty.NORMAL
        raw = (resp.choices[0].message.content or "").strip()
        data = parse_llm_json_response(raw)
        d = str(data.get("difficulty", "")).strip().lower()
        for m in TaskDifficulty:
            if m.value == d:
                return m
        # 中文容错：模型可能返回中文描述而非英文枚举值，映射到对应枚举。
        # 简单→SIMPLE（直接执行）；一般/普通→NORMAL（标准 ReAct）；
        # 中等→MEDIUM（基础规划）；复杂→COMPLEX（完整规划与多轮迭代）。
        zh_to_difficulty = {
            "简单": TaskDifficulty.SIMPLE,
            "一般": TaskDifficulty.NORMAL,
            "普通": TaskDifficulty.NORMAL,
            "中等": TaskDifficulty.MEDIUM,
            "复杂": TaskDifficulty.COMPLEX,
        }
        if d in zh_to_difficulty:
            return zh_to_difficulty[d]
    except Exception as e:
        safe_agent_debug_log(
            location="task_classifier.py:classify_task_difficulty",
            message="classifier_failed",
            data={"exc_type": type(e).__name__, "exc_msg": str(e)[:400]},
        )
        _logger.warning("任务难度分类失败，降级为 normal: %s", e)
    return TaskDifficulty.NORMAL


__all__ = [
    "TaskDifficulty",
    "task_classifier_enabled",
    "classify_task_difficulty",
    "planner_merge_for_difficulty",
    "default_step_thinking_for_difficulty",
    "exec_merge_for_simple_path",
]
