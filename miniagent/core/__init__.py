"""核心引擎模块

导出：
- 配置管理 (config)
- 规划器与执行器身份常量 (planner, executor)
- Agent 主入口 (agent)
"""

from miniagent.core.config import (
    MODEL_PROFILES,
    get_default_agent_config,
    get_default_model_config,
    apply_model_profile,
    merge_agent_config,
)
from miniagent.core.executor import AGENT_NAME, AGENT_IDENTITY
from miniagent.core.planner import AGENT_NAME as PLANNER_AGENT_NAME
from miniagent.core.agent import run_agent, run_pipeline

__all__ = [
    "MODEL_PROFILES",
    "get_default_agent_config",
    "get_default_model_config",
    "apply_model_profile",
    "merge_agent_config",
    "AGENT_NAME",
    "AGENT_IDENTITY",
    "PLANNER_AGENT_NAME",
    "run_agent",
    "run_pipeline",
]
