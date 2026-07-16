"""模型与 Agent 配置管理（JSON 配置）

Agent 配置只描述问答执行策略；模型与 provider 配置由 ``miniagent.llm`` 拥有。

配置优先级：包内 defaults → config.user.json

布尔项通过 ``get_config_bool`` 解析（兼容 JSON bool 与 ``"true"``/``"false"`` 字符串）。
``get_default_agent_config()`` 中部分字段为代码硬编码，不可经 JSON 修改；运行时请用
``merge_agent_config()`` 覆盖。会话与飞书运行参数只接受职责明确的分组结构。

敏感信息（API 密钥等）放在 config.user.json 的 secrets 部分，由 env_loader.py 加载到环境变量。

公开 API：``AGENT_NAME``、``get_default_agent_config``、``merge_agent_config``。

设计背景见 docs/ARCHITECTURE.md § 配置层。
"""

from __future__ import annotations

from typing import Any

from miniagent.agent.constants import DEFAULT_AGENT_MAX_TURNS, DEFAULT_AGENT_TOOL_TIMEOUT
from miniagent.agent.logging import get_logger
from miniagent.agent.settings import get_config, get_config_bool, get_config_section
from miniagent.agent.types.config import (
    AgentConfig,
    FeishuChannelConfig,
    SessionBindingConfig,
    normalize_conversation_history,
)

_logger = get_logger(__name__)


# Agent 显示名称（身份提示词等）
AGENT_NAME = "MiniAgent"

def _cfg_int(key: str, default: int) -> int:
    """读取整数配置项。"""
    return int(get_config(key, default))


def _cfg_bool(key: str, default: bool) -> bool:
    """读取布尔配置项（字符串 true/false 安全解析）。"""
    return get_config_bool(key, default)


def _cfg_float(key: str, default: float) -> float:
    """读取浮点配置项。"""
    return float(get_config(key, default))


def get_default_agent_config() -> AgentConfig:
    """获取默认 AgentConfig（从 JSON 配置加载）。

    **来自 JSON 的字段**（``agent.*`` / ``memory.history_progressive``）：

    - ``max_turns``, ``tool_timeout``, ``http_timeout``
    - ``context_reserve_ratio``, ``context_compress_threshold``
    - ``allow_parallel_tools``, ``debug``, ``log_token_usage``
    - ``loop_detection``（整节浅拷贝）
    - ``history_progressive_compression``（``memory.history_progressive``）

    **代码硬编码、不可通过 JSON 修改的字段**：

    - ``context_overflow_strategy`` = ``"summarize"``
    - ``compress_messages`` = ``True``
    - ``tool_selection_strategy`` = ``"toolbox"``
    - ``auto_execute_confirmed`` = ``False``
    - ``response_language`` = ``"zh-CN"``
    - ``response_format`` = ``"markdown"``
    - ``log_file`` = ``None``

    运行时覆盖请使用 ``merge_agent_config()`` 或 ``run_agent(options.agent_config)``。
    """
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
    """在基础配置上合并显式覆盖。

    ``session_config`` 与 ``feishu_config`` 接受分组字典并与基础分组逐字段合并；
    ``loop_detection`` 与 ``llm_overrides`` 增量合并，其余已知顶层键直接覆盖。
    未知键会被忽略并记录 debug 日志。

    Args:
        base: 基础 AgentConfig 对象
        overrides: 覆盖配置字典

    Returns:
        合并后的 AgentConfig 对象

    Example:
        >>> config = merge_agent_config(base, {
        ...     "session_config": {"session_key": "session-1"},
        ...     "feishu_config": {"receive_chat_id": "oc_abc"},
        ... })
    """
    session_config_dict: dict[str, Any] = {
        "session_key": base.session_config.session_key,
        "session_workspace": base.session_config.session_workspace,
        "session_registry": base.session_config.session_registry,
        "session_toolboxes": list(base.session_config.session_toolboxes),
        "conversation_history": list(base.session_config.conversation_history),
    }
    feishu_config_dict: dict[str, Any] = {
        "receive_chat_id": base.feishu_config.receive_chat_id,
        "trigger_message_id": base.feishu_config.trigger_message_id,
        "root_id": base.feishu_config.root_id,
        "parent_id": base.feishu_config.parent_id,
        "thread_id": base.feishu_config.thread_id,
        "im_receive_id_type": base.feishu_config.im_receive_id_type,
        "im_receive_id": base.feishu_config.im_receive_id,
        "cli_loop_state": base.feishu_config.cli_loop_state,
        "cli_dispatch_allow_mutations": base.feishu_config.cli_dispatch_allow_mutations,
    }
    merged_dict: dict[str, Any] = {
        # 核心配置
        "max_turns": base.max_turns,
        "tool_timeout": base.tool_timeout,
        "http_timeout": base.http_timeout,
        "allow_parallel_tools": base.allow_parallel_tools,
        "auto_execute_confirmed": base.auto_execute_confirmed,
        # 上下文配置
        "context_reserve_ratio": base.context_reserve_ratio,
        "context_compress_threshold": base.context_compress_threshold,
        "context_overflow_strategy": base.context_overflow_strategy,
        "compress_messages": base.compress_messages,
        # 输出配置
        "response_language": base.response_language,
        "response_format": base.response_format,
        # 调试配置
        "debug": base.debug,
        "log_token_usage": base.log_token_usage,
        "log_file": base.log_file,
        # 高级配置
        "tool_selection_strategy": base.tool_selection_strategy,
        "loop_detection": dict(base.loop_detection),
        "llm_overrides": dict(base.llm_overrides) if base.llm_overrides else {},
        "risk_level": base.risk_level,
        "history_progressive_compression": base.history_progressive_compression,
        "session_config": None,
        "feishu_config": None,
    }

    for key, value in overrides.items():
        if key == "session_config" and isinstance(value, dict):
            session_config_dict.update(value)
        elif key == "feishu_config" and isinstance(value, dict):
            feishu_config_dict.update(value)
        elif key == "loop_detection" and isinstance(value, dict):
            merged_dict["loop_detection"].update(value)
        elif key == "llm_overrides" and isinstance(value, dict):
            merged_dict["llm_overrides"].update(value)
        elif key in merged_dict:
            merged_dict[key] = value
        else:
            _logger.debug("merge_agent_config: 忽略未知覆盖键 %r", key)

    session_config_dict["conversation_history"] = normalize_conversation_history(
        session_config_dict.get("conversation_history")
    )
    merged_dict["session_config"] = SessionBindingConfig(**session_config_dict)
    merged_dict["feishu_config"] = FeishuChannelConfig(**feishu_config_dict)

    return AgentConfig(**merged_dict)


__all__ = [
    "AGENT_NAME",
    "get_default_agent_config",
    "merge_agent_config",
]
