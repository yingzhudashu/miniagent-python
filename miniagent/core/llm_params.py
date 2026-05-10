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
    """执行阶段（ReAct）chat.completions 参数。"""
    mc = get_default_model_config()
    ov = dict(agent_config.model_overrides)
    if merge_overrides:
        ov = {**ov, **merge_overrides}

    tl = str(ov.get("thinking_level", mc.thinking_level))
    tb = int(ov.get("thinking_budget", mc.thinking_budget))

    kw: dict[str, Any] = {
        "model": ov.get("model", mc.model),
        "temperature": float(ov.get("temperature", mc.temperature)),
        "max_tokens": int(ov.get("max_tokens", mc.max_tokens)),
        "top_p": float(ov.get("top_p", mc.top_p)),
        "stream": stream,
    }

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
    """规划阶段参数；默认较低温度与适中 max_tokens。"""
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
    }
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
