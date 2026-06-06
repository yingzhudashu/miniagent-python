"""核心引擎模块（规划 + 执行 + 配置）

职责边界：
- **本包**：LLM 驱动的两阶段编排（``agent``）、规划/执行（``planner`` / ``executor``）、
  配置合并（``config``）、调用参数与供应商适配（``llm_params``、``vendor/qwen_extra``）、
  任务预分类（``task_classifier``）、thinking 档位映射（``thinking_presets``）。共享 ``AsyncOpenAI`` 见 ``openai_client``（本 ``__init__`` 不导出）。
- **非本包**：主循环与通道在 ``miniagent.engine``；持久记忆在 ``miniagent.memory``。

两阶段与配置合并的细节见 ``docs/ARCHITECTURE.md``。

``__all__`` 仅聚合最常用的稳定入口；其余子模块请按需 ``import``。

**注意**：为避免循环导入（memory.context → infrastructure → core → executor → memory.context），
本 ``__init__.py`` 使用延迟导入，仅在首次访问时加载 agent/executor 等模块。
"""

# 常量模块不依赖其他模块，可以安全导入
# 配置模块不依赖 executor/memory，可以安全导入
from miniagent.core.config import (
    AGENT_NAME,
    get_default_agent_config,
    get_default_model_config,
    merge_agent_config,
)
from miniagent.core.constants import (  # noqa: F401
    AGENT_HISTORY_SIZE,
    AGENT_MAX_TURNS,
    AGENT_TOOL_TIMEOUT,
    DEFAULT_AGENT_MAX_TURNS,
    DEFAULT_AGENT_TOOL_TIMEOUT,
)

# 延迟导入的符号
_lazy_symbols = {
    "AGENT_IDENTITY": "miniagent.core.executor:AGENT_IDENTITY",
    "TaskDifficulty": "miniagent.core.task_classifier:TaskDifficulty",
    "execute_plan": "miniagent.core.executor:execute_plan",
    "generate_plan": "miniagent.core.planner:generate_plan",
    "run_agent": "miniagent.core.agent:run_agent",
    "run_pipeline": "miniagent.core.agent:run_pipeline",
}


def __getattr__(name: str):
    """延迟导入，避免循环导入问题。"""
    if name in _lazy_symbols:
        module_path, symbol = _lazy_symbols[name].rsplit(":", 1)
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, symbol)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AGENT_HISTORY_SIZE",
    "AGENT_IDENTITY",
    "AGENT_MAX_TURNS",
    "AGENT_NAME",
    "AGENT_TOOL_TIMEOUT",
    "DEFAULT_AGENT_MAX_TURNS",
    "DEFAULT_AGENT_TOOL_TIMEOUT",
    "TaskDifficulty",
    "execute_plan",
    "generate_plan",
    "get_default_agent_config",
    "get_default_model_config",
    "merge_agent_config",
    "run_agent",
    "run_pipeline",
]
