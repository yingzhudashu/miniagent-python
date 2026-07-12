"""将 ``ModelConfig`` / ``AgentConfig.model_overrides`` 合并为 ``chat.completions.create`` 的 kwargs。

- ``resolve_exec_completion_kwargs``：ReAct 执行阶段（含 ``stream``）。
- ``resolve_planner_completion_kwargs``：结构化规划阶段。

Qwen/DashScope 兼容端点通过 ``build_thinking_extra_body`` 注入 ``extra_body``。"""

from __future__ import annotations

from typing import Any

from miniagent.core.config import get_default_model_config
from miniagent.core.vendor.qwen_extra import build_thinking_extra_body
from miniagent.types.config import AgentConfig


def resolve_exec_completion_kwargs(
    agent_config: AgentConfig,
    *,
    stream: bool,
    merge_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行阶段（ReAct）chat.completions 参数。

    Args:
        agent_config: Agent 配置（含 model_overrides 覆盖默认值）。
        stream: 是否启用流式响应（CLI/飞书需要 True）。
        merge_overrides: 运行时额外覆盖（如特定工具调用时的参数调整）。

    Returns:
        供统一 LLM transport 消费的模型参数。
    """
    mc = get_default_model_config()
    ov = dict(agent_config.model_overrides)
    if merge_overrides:
        ov = {**ov, **merge_overrides}

    # thinking_level: 思考深度档位（none/low/medium/high），影响模型推理展开程度。
    tl = str(ov.get("thinking_level", mc.thinking_level))
    # thinking_budget: 思考预算（最大思考步数），控制模型自我反思迭代次数。
    tb = int(ov.get("thinking_budget", mc.thinking_budget))

    kw: dict[str, Any] = {
        "model": ov.get("model", mc.model),
        "temperature": float(ov.get("temperature", mc.temperature)),
        "max_tokens": int(ov.get("max_tokens", mc.max_tokens)),
        "top_p": float(ov.get("top_p", mc.top_p)),
        "stream": stream,
        "_thinking_level": tl,
        "_thinking_budget": tb,
    }

    # service_tier: 服务层级（auto/default/flex），控制延迟优先级
    service_tier = ov.get("service_tier", mc.service_tier)
    if service_tier:
        kw["service_tier"] = service_tier

    extra = build_thinking_extra_body(
        mc.base_url,
        tl,
        tb,
        model_overrides_extra=ov,
    )
    if extra:
        kw["extra_body"] = extra
    return kw


def resolve_planner_completion_kwargs(
    agent_config: AgentConfig | None,
    *,
    merge_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """规划阶段参数；默认较低温度与适中 max_tokens。

    规划阶段需要稳定输出结构化 JSON，故温度默认 0.3（低于执行阶段）。
    max_tokens 默认 2048，足够生成完整规划 JSON。

    Args:
        agent_config: Agent 配置（可选，无时使用全局默认）。
        merge_overrides: 运行时额外覆盖。

    Returns:
        供统一 LLM transport 消费的模型参数。
    """
    mc = get_default_model_config()
    ov: dict[str, Any] = dict(agent_config.model_overrides) if agent_config else {}
    if merge_overrides:
        ov = {**ov, **merge_overrides}

    tl = str(ov.get("thinking_level", mc.thinking_level))
    tb = int(ov.get("thinking_budget", mc.thinking_budget))

    kw: dict[str, Any] = {
        "model": ov.get("model", mc.model),
        "temperature": float(ov.get("planner_temperature", ov.get("temperature", 0.3))),
        "max_tokens": int(ov.get("planner_max_tokens", 2048)),
        "top_p": float(ov.get("top_p", mc.top_p)),
        "stream": False,
        "_thinking_level": tl,
        "_thinking_budget": tb,
    }

    # service_tier: 服务层级（auto/default/flex）
    service_tier = ov.get("service_tier", mc.service_tier)
    if service_tier:
        kw["service_tier"] = service_tier

    extra = build_thinking_extra_body(
        mc.base_url,
        tl,
        tb,
        model_overrides_extra=ov,
    )
    if extra:
        kw["extra_body"] = extra
    return kw


__all__ = ["resolve_exec_completion_kwargs", "resolve_planner_completion_kwargs"]
