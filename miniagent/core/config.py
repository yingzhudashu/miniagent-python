"""模型与 Agent 配置管理（环境变量 + 可选外部 JSON 补丁）

- **模型层** ``ModelConfig``：端点、温度、``max_tokens``、thinking 等。
- **Agent 层** ``AgentConfig``：``max_turns``、工具超时、上下文压缩阈值、循环检测等。

扁平环境变量优先；``external_config`` 在启动时加载的补丁由 ``get_default_model_config`` 等与 env 合并
（thinking 以 env 为准覆盖补丁）。``MODEL_PROFILES`` 提供预设别名。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.types.config import (
    AgentConfig,
    ModelConfig,
    ModelProfile,
    normalize_conversation_history,
)

_logger = get_logger(__name__)


# ============================================================================
# 模型配置预设
# ============================================================================

MODEL_PROFILES: dict[str, ModelProfile] = {
    "creative": ModelProfile(
        name="creative",
        temperature=0.9,
        top_p=1.0,
        max_tokens=8192,
        thinking_level="disabled",
        thinking_budget=0,
        description="高创造性任务：写作、头脑风暴、创意生成",
    ),
    "balanced": ModelProfile(
        name="balanced",
        temperature=0.7,
        top_p=1.0,
        max_tokens=4096,
        thinking_level="light",
        thinking_budget=1024,
        description="平衡模式：日常任务、通用问答（默认）",
    ),
    "precise": ModelProfile(
        name="precise",
        temperature=0.3,
        top_p=0.9,
        max_tokens=4096,
        thinking_level="medium",
        thinking_budget=2048,
        description="精确模式：数据分析、代码审查、事实查询",
    ),
    "code": ModelProfile(
        name="code",
        temperature=0.2,
        top_p=0.9,
        max_tokens=8192,
        thinking_level="light",
        thinking_budget=2048,
        description="编程模式：代码生成、调试、重构",
    ),
    "fast": ModelProfile(
        name="fast",
        temperature=0.3,
        top_p=0.9,
        max_tokens=2048,
        thinking_level="disabled",
        thinking_budget=0,
        description="快速模式：简单问答、快速查询",
    ),
}


# ============================================================================
# 环境变量辅助函数
# ============================================================================


def _env_int(key: str, fallback: int) -> int:
    """从环境变量读取整数值"""
    v = os.environ.get(key)
    if v is not None:
        try:
            return int(v)
        except ValueError:
            pass
    return fallback


def _env_bool(key: str, fallback: bool) -> bool:
    """从环境变量读取布尔值"""
    v = os.environ.get(key)
    if v is not None:
        return v.lower() in ("true", "1", "yes")
    return fallback


def _env_float(key: str, fallback: float) -> float:
    """从环境变量读取浮点值"""
    v = os.environ.get(key)
    if v is not None:
        try:
            return float(v)
        except ValueError:
            pass
    return fallback


# ============================================================================
# 默认配置工厂
# ============================================================================

# 循环检测默认配置
DEFAULT_LOOP_DETECTION: dict[str, Any] = {
    "enabled": _env_bool("LOOP_DETECTION_ENABLED", True),
    "history_size": _env_int("LOOP_HISTORY_SIZE", 50),
    "warning_threshold": _env_int("LOOP_WARNING_THRESHOLD", 8),
    "critical_threshold": _env_int("LOOP_CRITICAL_THRESHOLD", 12),
    "detectors": {
        "generic_repeat": True,
        "known_poll_no_progress": True,
        "ping_pong": True,
    },
}


def get_default_model_config() -> ModelConfig:
    """获取默认 ModelConfig

    根据环境变量和预设构建完整的模型配置。
    读取的环境变量：OPENAI_BASE_URL, OPENAI_MODEL, MODEL_PROFILE, AGENT_CONTEXT_WINDOW,
    OPENAI_MAX_TOKENS（可选，覆盖输出 max_tokens）、AGENT_THINKING_DEFAULT（low/medium/high）、
    OPENAI_THINKING_BUDGET（非负整数，覆盖当前模型的 thinking 预算）。

    合并优先级（thinking）：``MODEL_PROFILE`` 为基线；若设置了 ``AGENT_THINKING_DEFAULT`` 则用之；
    否则若存在可选外部 JSON 补丁中的 ``thinking_default`` 则用之；否则保持基线。

    ``OPENAI_THINKING_BUDGET`` 若显式设置且可解析为非负整数，则覆盖上述推导的预算
    （与 ``AGENT_THINKING_DEFAULT`` 同时设置时，**最终 thinking 预算以此为准**，档位仍由后者决定）。
    若**未**设置 ``AGENT_THINKING_DEFAULT``，则还可使用外部 JSON 的 ``thinking_budget_by_model``
    覆盖当前模型的预算；一旦设置了 ``AGENT_THINKING_DEFAULT``，则不再用该按模型项（避免与 env 档位冲突）。

    可选 ``MINIAGENT_CONFIG`` / ``MINIAGENT_OPENCLAW_CONFIG`` 仅作兼容：在对应扁平环境变量
    **未设置** 时回填 ``OPENAI_*`` 等；thinking 相关仍以**环境变量优先于补丁**。

    Returns:
        默认的模型配置对象
    """
    from miniagent.core.thinking_presets import map_openclaw_thinking_to_model
    from miniagent.runtime.external_config import get_external_config_patch

    def _limits_for_current_model(e: dict[str, Any], mname: str) -> dict[str, int] | None:
        ml = e.get("model_limits")
        if not isinstance(ml, dict):
            return None
        for key in (mname, mname.split("/")[-1]):
            if key and isinstance(ml.get(key), dict):
                ent = ml[key]
                if isinstance(ent, dict):
                    return {k: int(v) for k, v in ent.items() if isinstance(v, int)}
        return None

    profile_name = os.environ.get("MODEL_PROFILE", "balanced")
    preset = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
    ext = get_external_config_patch()

    thinking_level = preset.thinking_level
    thinking_budget = preset.thinking_budget

    env_td = (os.environ.get("AGENT_THINKING_DEFAULT") or "").strip().lower()
    thinking_default_from_env = env_td in ("low", "medium", "high")
    if thinking_default_from_env:
        thinking_level, thinking_budget = map_openclaw_thinking_to_model(env_td)
    else:
        td = ext.get("thinking_default")
        if isinstance(td, str):
            tdn = td.strip().lower()
            if tdn in ("low", "medium", "high"):
                thinking_level, thinking_budget = map_openclaw_thinking_to_model(tdn)

    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    budget_raw = os.environ.get("OPENAI_THINKING_BUDGET")
    budget_from_env: int | None = None
    if budget_raw is not None and str(budget_raw).strip() != "":
        try:
            b = int(str(budget_raw).strip())
            if b >= 0:
                budget_from_env = b
        except ValueError:
            pass

    if budget_from_env is not None:
        thinking_budget = budget_from_env
    elif not thinking_default_from_env:
        bym = ext.get("thinking_budget_by_model")
        if isinstance(bym, dict):
            if model_name in bym and isinstance(bym[model_name], int):
                thinking_budget = int(bym[model_name])
            else:
                short = model_name.split("/")[-1]
                if short != model_name and short in bym and isinstance(bym[short], int):
                    thinking_budget = int(bym[short])

    limits_lm = _limits_for_current_model(ext, model_name)

    if "AGENT_CONTEXT_WINDOW" in os.environ:
        context_window = _env_int("AGENT_CONTEXT_WINDOW", 128000)
    elif limits_lm and limits_lm.get("context_window"):
        context_window = int(limits_lm["context_window"])
    else:
        context_window = 128000

    if "OPENAI_MAX_TOKENS" in os.environ:
        max_tokens = _env_int("OPENAI_MAX_TOKENS", preset.max_tokens)
    elif limits_lm and limits_lm.get("max_tokens"):
        max_tokens = int(limits_lm["max_tokens"])
    else:
        max_tokens = preset.max_tokens

    return ModelConfig(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=model_name,
        temperature=preset.temperature,
        top_p=preset.top_p,
        max_tokens=max_tokens,
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        context_window=context_window,
        stream=False,
        retry_count=2,
        profiles=MODEL_PROFILES,
        active_profile=profile_name,
    )


def get_default_agent_config() -> AgentConfig:
    """获取默认 AgentConfig

    支持环境变量覆盖：
    - AGENT_MAX_TURNS: 最大对话轮数（默认 200）
    - AGENT_TOOL_TIMEOUT: 工具超时秒数（默认 60）
    - AGENT_HTTP_TIMEOUT: HTTP 超时秒数（默认 120）
    - AGENT_CONTEXT_RESERVE: 上下文预留比例（默认 0.15）
    - AGENT_CONTEXT_COMPRESS_THRESHOLD: 压缩触发阈值（默认 0.6）
    - AGENT_DEBUG: 调试模式
    - AGENT_LOG_TOKEN_USAGE: 记录 token 使用量

    Returns:
        默认的 Agent 配置对象
    """
    return AgentConfig(
        max_turns=_env_int("AGENT_MAX_TURNS", 200),
        tool_timeout=_env_int("AGENT_TOOL_TIMEOUT", 60),
        http_timeout=_env_int("AGENT_HTTP_TIMEOUT", 120),
        context_reserve_ratio=_env_float("AGENT_CONTEXT_RESERVE", 0.15),
        context_compress_threshold=_env_float(
            "AGENT_CONTEXT_COMPRESS_THRESHOLD", 0.6
        ),
        context_overflow_strategy="summarize",
        compress_messages=True,
        tool_selection_strategy="toolbox",
        auto_execute_confirmed=False,
        allow_parallel_tools=True,
        response_language="zh-CN",
        response_format="markdown",
        debug=_env_bool("AGENT_DEBUG", False),
        log_token_usage=_env_bool("AGENT_LOG_TOKEN_USAGE", True),
        log_file=None,
        loop_detection=dict(DEFAULT_LOOP_DETECTION),
    )


def apply_model_profile(config: ModelConfig, profile_name: str) -> ModelConfig:
    """应用模型预设到 ModelConfig

    将指定预设的参数（temperature、top_p、max_tokens、thinking 等）
    合并到现有配置中。未知预设名称会自动回退到 balanced。

    Args:
        config: 当前模型配置
        profile_name: 预设名称（creative/balanced/precise/code/fast）

    Returns:
        应用预设后的新配置
    """
    profile = MODEL_PROFILES.get(profile_name)
    if not profile:
        _logger.warning("未知模型预设: %s，使用 balanced", profile_name)
        return apply_model_profile(config, "balanced")

    return ModelConfig(
        base_url=config.base_url,
        model=config.model,
        temperature=profile.temperature,
        top_p=profile.top_p,
        max_tokens=profile.max_tokens,
        thinking_level=profile.thinking_level,
        thinking_budget=profile.thinking_budget,
        context_window=config.context_window,
        stream=config.stream,
        retry_count=config.retry_count,
        profiles=config.profiles,
        active_profile=profile_name,
    )


def merge_agent_config(
    base: AgentConfig, overrides: dict[str, Any]
) -> AgentConfig:
    """合并 Agent 配置

    将覆盖配置合并到基础配置中。loop_detection 会逐字段合并，
    确保未指定的子字段保留原值。

    Args:
        base: 基础配置（通常为 get_default_agent_config() 的结果）
        overrides: 要覆盖的字段

    Returns:
        合并后的完整配置
    """
    # 浅拷贝基础配置
    merged_dict = {
        "max_turns": base.max_turns,
        "tool_timeout": base.tool_timeout,
        "http_timeout": base.http_timeout,
        "context_reserve_ratio": base.context_reserve_ratio,
        "context_compress_threshold": base.context_compress_threshold,
        "context_overflow_strategy": base.context_overflow_strategy,
        "compress_messages": base.compress_messages,
        "tool_selection_strategy": base.tool_selection_strategy,
        "auto_execute_confirmed": base.auto_execute_confirmed,
        "allow_parallel_tools": base.allow_parallel_tools,
        "response_language": base.response_language,
        "response_format": base.response_format,
        "debug": base.debug,
        "log_token_usage": base.log_token_usage,
        "log_file": base.log_file,
        "loop_detection": dict(base.loop_detection),
        "model_overrides": dict(base.model_overrides),
        "session_key": base.session_key,
        "session_workspace": base.session_workspace,
        "session_registry": base.session_registry,
        "session_toolboxes": list(base.session_toolboxes),
        "conversation_history": list(base.conversation_history),
        "risk_level": base.risk_level,
        "cli_loop_state": base.cli_loop_state,
        "cli_dispatch_allow_mutations": base.cli_dispatch_allow_mutations,
    }

    # 应用覆盖
    for key, value in overrides.items():
        if key == "loop_detection" and isinstance(value, dict):
            merged_dict["loop_detection"].update(value)
        elif key == "model_overrides" and isinstance(value, dict):
            merged_dict["model_overrides"].update(value)
        elif key in merged_dict:
            if key == "conversation_history":
                merged_dict[key] = normalize_conversation_history(value)
            else:
                merged_dict[key] = value

    return AgentConfig(**merged_dict)


__all__ = [
    "MODEL_PROFILES",
    "DEFAULT_LOOP_DETECTION",
    "get_default_model_config",
    "get_default_agent_config",
    "apply_model_profile",
    "merge_agent_config",
]
