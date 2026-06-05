"""模型与 Agent 配置管理（JSON配置优先）

- **模型层** ``ModelConfig``：端点、温度、``max_tokens``、thinking 等。
- **Agent 层** ``AgentConfig``：``max_turns``、工具超时、上下文压缩阈值、循环检测等。

配置优先级（从低到高）：
1. config.defaults.json - 默认配置（随代码发布）
2. config.user.json - 用户配置
3. 环境变量 - 最高优先级（运行时覆盖）

敏感信息（API密钥等）放在config.user.json的secrets部分，由env_loader.py加载到环境变量。
"""

from __future__ import annotations

from typing import Any

from miniagent.infrastructure.json_config import get_config, get_config_section
from miniagent.infrastructure.logger import get_logger
from miniagent.types.config import (
    AgentConfig,
    ModelConfig,
    normalize_conversation_history,
)

_logger = get_logger(__name__)


# ============================================================================
# 全局 Agent 名称
# ============================================================================
AGENT_NAME = "MiniAgent"


# ============================================================================
# 配置获取辅助函数
# ============================================================================
def _cfg_str(key: str, default: str) -> str:
    """从JSON配置读取字符串"""
    return str(get_config(key, default))


def _cfg_int(key: str, default: int) -> int:
    """从JSON配置读取整数"""
    return int(get_config(key, default))


def _cfg_bool(key: str, default: bool) -> bool:
    """从JSON配置读取布尔值"""
    return bool(get_config(key, default))


def _cfg_float(key: str, default: float) -> float:
    """从JSON配置读取浮点数"""
    return float(get_config(key, default))


# ============================================================================
# 默认配置工厂
# ============================================================================

def get_default_model_config() -> ModelConfig:
    """获取默认 ModelConfig

    配置优先级：JSON配置 → 环境变量覆盖。
    读取的配置项：model.base_url, model.model, model.temperature, model.top_p,
    model.max_tokens, model.thinking_level, model.thinking_budget, model.context_window,
    model.stream, model.retry_count。

    特殊处理：当thinking_level设置为low/medium/high时，thinking_budget自动映射
    （除非显式设置了thinking_budget）。

    Returns:
        默认的模型配置对象
    """
    from miniagent.core.thinking_presets import map_thinking_level_to_model

    # 获取thinking配置
    thinking_level_raw = _cfg_str("model.thinking_level", "light")

    # 检查是否显式设置了thinking_budget（通过环境变量或MINIAGENT_CONFIG）
    import json
    import os
    explicit_budget = None

    # 检查单项环境变量
    budget_env = os.environ.get("MINIAGENT_MODEL_THINKING_BUDGET")
    if budget_env is not None:
        try:
            explicit_budget = int(budget_env)
        except ValueError as e:
            _logger.debug("解析thinking_budget环境变量失败: %s", e)

    # 检查MINIAGENT_CONFIG中的thinking_budget
    if explicit_budget is None:
        config_json_str = os.environ.get("MINIAGENT_CONFIG", "")
        if config_json_str.strip():
            try:
                config_json = json.loads(config_json_str)
                if isinstance(config_json, dict):
                    model_config = config_json.get("model", {})
                    if isinstance(model_config, dict) and "thinking_budget" in model_config:
                        explicit_budget = int(model_config["thinking_budget"])
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                _logger.debug("解析MINIAGENT_CONFIG中的thinking_budget失败: %s", e)

    # 应用thinking_level映射
    if thinking_level_raw.lower() in ("low", "medium", "high"):
        mapped_level, mapped_budget = map_thinking_level_to_model(thinking_level_raw)
        thinking_level = mapped_level
        thinking_budget = explicit_budget if explicit_budget is not None else mapped_budget
    else:
        thinking_level = thinking_level_raw
        thinking_budget = explicit_budget if explicit_budget is not None else _cfg_int("model.thinking_budget", 1024)

    return ModelConfig(
        base_url=_cfg_str("model.base_url", "https://api.openai.com/v1"),
        model=_cfg_str("model.model", "gpt-4o-mini"),
        temperature=_cfg_float("model.temperature", 0.7),
        top_p=_cfg_float("model.top_p", 1.0),
        max_tokens=_cfg_int("model.max_tokens", 4096),
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        context_window=_cfg_int("model.context_window", 128000),
        stream=_cfg_bool("model.stream", False),
        retry_count=_cfg_int("model.retry_count", 2),
    )


def get_default_agent_config() -> AgentConfig:
    """获取默认 AgentConfig

    配置优先级：JSON配置 → 环境变量覆盖。
    支持的配置项见 config.defaults.json 中 agent 和 memory 配置节。

    Returns:
        默认的 Agent 配置对象
    """
    # 获取agent配置section，然后从中获取loop_detection
    agent_section = get_config_section("agent")
    loop_detection = dict(agent_section.get("loop_detection", {}))

    return AgentConfig(
        max_turns=_cfg_int("agent.max_turns", 400),
        tool_timeout=_cfg_int("agent.tool_timeout", 60),
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
    "get_default_model_config",
    "get_default_agent_config",
    "merge_agent_config",
]
