"""核心引擎模块（规划 + 执行 + 配置）

职责边界：
- **本包**：LLM 驱动的两阶段编排（``agent``）、规划/执行实现（``planner`` / ``executor``）、
  合并后的 ``AgentConfig``（``config``）；共享 ``AsyncOpenAI`` 工厂在邻接模块
  ``miniagent.core.openai_client``（本 ``__init__`` 不导出，避免与显式 ``client=`` 注入混淆）。
- **非本包**：运行时主循环、CLI、飞书、消息队列在 ``miniagent.engine``；磁盘记忆在 ``miniagent.memory``。

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
