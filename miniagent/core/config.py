"""模型与 Agent 配置管理（扁平环境变量）

- **模型层** ``ModelConfig``：端点、温度、``max_tokens``、thinking 等。
- **Agent 层** ``AgentConfig``：``max_turns``、工具超时、上下文压缩阈值、循环检测等。

所有参数通过环境变量直接设置，无预设层级。
``AGENT_THINKING_DEFAULT`` / ``OPENAI_THINKING_BUDGET`` 控制 thinking 行为。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.types.config import (
    AgentConfig,
    ModelConfig,
    normalize_conversation_history,
)

_logger = get_logger(__name__)


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

# 全局 Agent 名称
AGENT_NAME = "MiniAgent"

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

    所有参数通过环境变量直接设置。
    读取的环境变量：OPENAI_BASE_URL, OPENAI_MODEL, AGENT_CONTEXT_WINDOW,
    OPENAI_MAX_TOKENS、AGENT_THINKING_DEFAULT（low/medium/high）、
    OPENAI_THINKING_BUDGET（非负整数，覆盖 thinking 预算）。

    Returns:
        默认的模型配置对象
    """
    from miniagent.core.thinking_presets import map_openclaw_thinking_to_model

    thinking_level = "light"
    thinking_budget = 1024

    env_td = (os.environ.get("AGENT_THINKING_DEFAULT") or "").strip().lower()
    if env_td in ("low", "medium", "high"):
        thinking_level, thinking_budget = map_openclaw_thinking_to_model(env_td)

    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    budget_raw = os.environ.get("OPENAI_THINKING_BUDGET")
    if budget_raw is not None and str(budget_raw).strip() != "":
        try:
            b = int(str(budget_raw).strip())
            if b >= 0:
                thinking_budget = b
        except ValueError:
            pass

    context_window = (
        _env_int("AGENT_CONTEXT_WINDOW", 128000) if "AGENT_CONTEXT_WINDOW" in os.environ else 128000
    )
    max_tokens = (
        _env_int("OPENAI_MAX_TOKENS", 4096)
        if "OPENAI_MAX_TOKENS" in os.environ
        else 4096
    )
    temperature = _env_float("AGENT_TEMPERATURE", 0.7)
    top_p = _env_float("AGENT_TOP_P", 1.0)

    return ModelConfig(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        context_window=context_window,
        stream=False,
        retry_count=2,
    )


def get_default_agent_config() -> AgentConfig:
    """获取默认 AgentConfig

    支持环境变量覆盖：
    - AGENT_MAX_TURNS: 最大对话轮数（默认 400）
    - AGENT_TOOL_TIMEOUT: 工具超时秒数（默认 60）
    - AGENT_HTTP_TIMEOUT: HTTP 超时秒数（默认 120）
    - AGENT_CONTEXT_RESERVE: 上下文预留比例（默认 0.15）
    - AGENT_CONTEXT_COMPRESS_THRESHOLD: 压缩触发阈值（默认 0.6）
    - AGENT_DEBUG: 调试模式
    - AGENT_LOG_TOKEN_USAGE: 记录 token 使用量
    - MINI_AGENT_HISTORY_PROGRESSIVE: 磁盘会话渐进压缩 L1–L3（与 ``history_progressive_compression`` 一致）
    - MINI_AGENT_HISTORY_MAINTENANCE_MAX_ITERS: 每轮用户消息后历史维护循环上限
    - MINI_AGENT_CONTEXT_TOOL_REDACT: 执行期 ContextManager 是否在摘要前逐条 redact ``tool`` 消息

    Returns:
        默认的 Agent 配置对象
    """
    return AgentConfig(
        max_turns=_env_int("AGENT_MAX_TURNS", 400),
        tool_timeout=_env_int("AGENT_TOOL_TIMEOUT", 60),
        http_timeout=_env_int("AGENT_HTTP_TIMEOUT", 120),
        context_reserve_ratio=_env_float("AGENT_CONTEXT_RESERVE", 0.15),
        context_compress_threshold=_env_float("AGENT_CONTEXT_COMPRESS_THRESHOLD", 0.6),
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
        history_progressive_compression=_env_bool("MINI_AGENT_HISTORY_PROGRESSIVE", True),
    )


def merge_agent_config(base: AgentConfig, overrides: dict[str, Any]) -> AgentConfig:
    """合并 Agent 配置

    将覆盖配置合并到基础配置中。loop_detection 会逐字段合并，
    确保未指定的子字段保留原值。

    注意：此函数手动列出 AgentConfig 的每个字段，原因是需要对 conversation_history
    等特殊字段进行规范化处理（而非简单的字典合并）。如果 AgentConfig 新增字段，
    必须同步更新下方的 merged_dict 构造和 elif key in merged_dict 分支。

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
        "model_overrides": dict(base.model_overrides) if base.model_overrides else {},
        "session_key": base.session_key,
        "session_workspace": base.session_workspace,
        "session_registry": base.session_registry,
        "session_toolboxes": list(base.session_toolboxes) if base.session_toolboxes else [],
        "conversation_history": list(base.conversation_history),
        "risk_level": base.risk_level,
        "cli_loop_state": base.cli_loop_state,
        "cli_dispatch_allow_mutations": base.cli_dispatch_allow_mutations,
        "feishu_receive_chat_id": base.feishu_receive_chat_id,
        "feishu_trigger_message_id": base.feishu_trigger_message_id,
        "feishu_root_id": base.feishu_root_id,
        "feishu_parent_id": base.feishu_parent_id,
        "feishu_thread_id": base.feishu_thread_id,
        "feishu_im_receive_id_type": base.feishu_im_receive_id_type,
        "feishu_im_receive_id": base.feishu_im_receive_id,
        "history_progressive_compression": base.history_progressive_compression,
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
    "AGENT_NAME",
    "DEFAULT_LOOP_DETECTION",
    "get_default_model_config",
    "get_default_agent_config",
    "merge_agent_config",
]
