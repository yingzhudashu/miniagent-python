"""任务难度轻量分类（可选）：在结构化规划前估算 simple/normal/medium/complex。

``MINIAGENT_TASK_CLASSIFIER`` 默认开启；简单任务可跳过 Phase 1 规划并由 ``agent`` 下调 thinking。
失败时回落启发式或 JSON 解析兜底；与 ``thinking_presets``、``llm_params`` 协同。"""

from __future__ import annotations

import json
import os
from enum import Enum
from typing import Any

from miniagent.core.openai_client import get_shared_async_openai
from miniagent.core.thinking_presets import map_business_depth, map_openclaw_thinking_to_model
from miniagent.infrastructure.logger import get_logger
from miniagent.types.config import AgentConfig

_logger = get_logger(__name__)


from miniagent.core._openai_compat import json_object_unsupported as _json_object_unsupported


class TaskDifficulty(str, Enum):
    """任务难度离散档位，与规划/执行 thinking 档位映射共用。"""

    SIMPLE = "simple"
    NORMAL = "normal"
    MEDIUM = "medium"
    COMPLEX = "complex"


def task_classifier_enabled() -> bool:
    """是否启用规划前难度分类（``MINIAGENT_TASK_CLASSIFIER``，默认开启）。"""
    v = os.environ.get("MINIAGENT_TASK_CLASSIFIER", "1")
    return str(v).strip().lower() in ("1", "true", "yes")


def planner_merge_for_difficulty(d: TaskDifficulty) -> dict[str, Any]:
    """规划阶段 model_overrides 合并片段（thinking_level / thinking_budget）。"""
    key = "low"
    if d == TaskDifficulty.NORMAL:
        key = "low"
    elif d == TaskDifficulty.MEDIUM:
        key = "medium"
    elif d == TaskDifficulty.COMPLEX:
        key = "high"
    elif d == TaskDifficulty.SIMPLE:
        key = "low"
    tl, tb = map_openclaw_thinking_to_model(key)
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
    """一次低开销 LLM 调用；失败返回 NORMAL。"""
    from miniagent.core.llm_params import resolve_planner_completion_kwargs

    sys_prompt = (
        "你是任务难度分类器。根据用户诉求与可用工具箱 id 列表，判断复杂度。\n"
        '只返回 JSON 对象：{"difficulty":"simple|normal|medium|complex"}\n'
        "simple：单步可答、无需工具或极简单查询；normal：常规多步但清晰；"
        "medium：需多工具协作或中等推理；complex：长链路、强依赖工具或高风险。"
    )
    tb_line = ", ".join(toolbox_ids[:32]) if toolbox_ids else "(无)"
    user_msg = f"用户诉求:\n{user_input}\n\n工具箱 id: {tb_line}"

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
    use_json_object = True
    resp = None
    try:
        for _attempt in range(2):
            try:
                create_args: dict[str, Any] = {**kw, "messages": messages}  # type: ignore[typeddict-item]
                if use_json_object:
                    create_args["response_format"] = {"type": "json_object"}
                # #region agent log
                try:
                    from miniagent.infrastructure.debug_ndjson import agent_debug_log

                    agent_debug_log(
                        hypothesis_id="B",
                        location="task_classifier.py:classify_task_difficulty",
                        message="before_chat_completions",
                        data={
                            "attempt": _attempt,
                            "model": kw.get("model"),
                            "json_object": use_json_object,
                        },
                    )
                except Exception:
                    pass
                # #endregion
                resp = await llm.chat.completions.create(**create_args)
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
        data = json.loads(raw)
        d = str(data.get("difficulty", "")).strip().lower()
        for m in TaskDifficulty:
            if m.value == d:
                return m
        # 中文容错
        if d in ("简单",):
            return TaskDifficulty.SIMPLE
        if d in ("一般", "普通"):
            return TaskDifficulty.NORMAL
        if d in ("中等",):
            return TaskDifficulty.MEDIUM
        if d in ("复杂",):
            return TaskDifficulty.COMPLEX
    except Exception as e:
        # #region agent log
        try:
            from miniagent.infrastructure.debug_ndjson import agent_debug_log

            agent_debug_log(
                hypothesis_id="B",
                location="task_classifier.py:classify_task_difficulty",
                message="classifier_failed",
                data={"exc_type": type(e).__name__, "exc_msg": str(e)[:400]},
            )
        except Exception:
            pass
        # #endregion
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
