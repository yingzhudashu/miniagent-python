"""模型与 Agent 配置管理（JSON 配置）

- **模型层** ``ModelConfig``：端点、温度、``max_tokens``、thinking 等。
- **Agent 层** ``AgentConfig``：``max_turns``、工具超时、上下文压缩阈值、循环检测等。

配置优先级：config.defaults.json → config.user.json

敏感信息（API 密钥等）放在 config.user.json 的 secrets 部分，由 env_loader.py 加载到环境变量。
"""

from __future__ import annotations

from typing import Any

from miniagent.core.constants import DEFAULT_AGENT_MAX_TURNS, DEFAULT_AGENT_TOOL_TIMEOUT
from miniagent.infrastructure.json_config import get_config, get_config_section
from miniagent.infrastructure.logger import get_logger
from miniagent.types.config import (
    AgentConfig,
    ModelConfig,
    normalize_conversation_history,
)

_logger = get_logger(__name__)


AGENT_NAME = "MiniAgent"


def _cfg_str(key: str, default: str) -> str:
    return str(get_config(key, default))


def _cfg_int(key: str, default: int) -> int:
    return int(get_config(key, default))


def _cfg_bool(key: str, default: bool) -> bool:
    return bool(get_config(key, default))


def _cfg_float(key: str, default: float) -> float:
    return float(get_config(key, default))


def get_default_model_config() -> ModelConfig:
    """获取默认 ModelConfig（从 JSON 配置加载）。"""
    from miniagent.core.thinking_presets import map_thinking_level_to_model

    thinking_level_raw = _cfg_str("model.thinking_level", "light")
    explicit_budget = None
    from miniagent.infrastructure.json_config import JsonConfigLoader

    JsonConfigLoader.get_instance()._load()
    user_model = JsonConfigLoader.get_instance()._user.get("model", {})
    if isinstance(user_model, dict) and "thinking_budget" in user_model:
        try:
            explicit_budget = int(user_model["thinking_budget"])
        except (TypeError, ValueError) as e:
            _logger.debug("解析 thinking_budget 失败: %s", e)

    if thinking_level_raw.lower() in ("low", "medium", "high"):
        mapped_level, mapped_budget = map_thinking_level_to_model(thinking_level_raw)
        thinking_level = mapped_level
        thinking_budget = explicit_budget if explicit_budget is not None else mapped_budget
    else:
        thinking_level = thinking_level_raw
        thinking_budget = (
            explicit_budget if explicit_budget is not None else _cfg_int("model.thinking_budget", 1024)
        )

    return ModelConfig(
        base_url=_cfg_str("model.base_url", "https://api.openai.com/v1"),
        model=_cfg_str("model.model", "gpt-4o-mini"),
        temperature=_cfg_float("model.temperature", 0.7),
        top_p=_cfg_float("model.top_p", 1.0),
        max_tokens=_cfg_int("model.max_tokens", 4096),
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        context_window=_cfg_int("model.context_window", 128000),
        retry_count=_cfg_int("model.retry_count", 2),
    )


def get_default_agent_config() -> AgentConfig:
    """获取默认 AgentConfig（从 JSON 配置加载）。"""
    agent_section = get_config_section("agent")
    loop_detection = dict(agent_section.get("loop_detection", {}))

    return AgentConfig(
        max_turns=_cfg_int("agent.max_turns", DEFAULT_AGENT_MAX_TURNS),
        tool_timeout=_cfg_int("agent.tool_timeout", DEFAULT_AGENT_TOOL_TIMEOUT),
        http_timeout=_cfg_int("agent.http_timeout", 120),
        context_reserve_ratio=_cfg_float("agent.context_reserve_ratio", 0.15),
        context_compress_threshold=_cfg_float("agent.context_compress_threshold", 0.6),
        context_overflow_strategy="summarize",
        compress_messages=True,
        tool_selection_strategy="toolbox",
        auto_execute_confirmed=False,
        allow_parallel_tools=_cfg_bool("agent.allow_parallel_tools", True),
        response_language="zh-CN",
        response_format="markdown",
        debug=_cfg_bool("agent.debug", False),
        log_token_usage=_cfg_bool("agent.log_token_usage", True),
        log_file=None,
        loop_detection=loop_detection,
        history_progressive_compression=_cfg_bool("memory.history_progressive", True),
    )


def merge_agent_config(base: AgentConfig, overrides: dict[str, Any]) -> AgentConfig:
    """合并 Agent 配置。"""
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
    "get_default_model_config",
    "get_default_agent_config",
    "merge_agent_config",
]
