"""核心引擎模块（规划 + 执行 + 配置）

职责边界：
- **本包**：LLM 驱动的两阶段编排（``agent``）、规划/执行（``planner`` / ``executor``）、
  配置合并（``config``）、调用参数与供应商适配（``llm_params``、``vendor/qwen_extra``）、
  任务预分类（``task_classifier``）、thinking 档位映射（``thinking_presets``）、
  自我优化子包（``self_opt``）。共享 ``AsyncOpenAI`` 见 ``openai_client``（本 ``__init__`` 不导出）。
- **非本包**：主循环与通道在 ``miniagent.engine``；持久记忆在 ``miniagent.memory``。

两阶段与配置合并的细节见 ``docs/ARCHITECTURE.md``；自我优化子包另见 ``docs/SELF_OPT.md``。

``__all__`` 仅聚合最常用的稳定入口；其余子模块请按需 ``import``。
"""

from miniagent.core.agent import run_agent, run_pipeline
from miniagent.core.config import (
    get_default_agent_config,
    get_default_model_config,
    merge_agent_config,
)
from miniagent.core.executor import AGENT_IDENTITY, AGENT_NAME
from miniagent.core.planner import AGENT_NAME as PLANNER_AGENT_NAME

__all__ = [
    "get_default_agent_config",
    "get_default_model_config",
    "merge_agent_config",
    "AGENT_NAME",
    "AGENT_IDENTITY",
    "PLANNER_AGENT_NAME",
    "run_agent",
    "run_pipeline",
]
