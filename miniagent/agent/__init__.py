"""高质量问答 Agent 核心。

职责边界：
- **本包**：分类、澄清、规划、ReAct 执行、反思、提示词、通用工具契约与注入端口。
- **公开入口**：``Agent(AgentServices)`` 与 ``Agent.run(AgentRequest)``。
- **非本包**：主循环与通道在 ``miniagent.assistant.engine``；持久记忆在 ``miniagent.assistant.memory``。

完整问答流水线与配置合并细节见 ``docs/ARCHITECTURE.md``。

``__all__`` 仅聚合最常用的稳定入口；其余子模块请按需 ``import``。

本 ``__init__.py`` 使用延迟导入，仅在首次访问时加载规划和执行实现。
"""

# 常量模块不依赖其他模块，可以安全导入
# 配置模块不依赖 executor/memory，可以安全导入
from miniagent.agent.config import (
    AGENT_NAME,
    get_default_agent_config,
    merge_agent_config,
)
from miniagent.agent.constants import (  # noqa: F401
    DEFAULT_AGENT_MAX_TURNS,
    DEFAULT_AGENT_TOOL_TIMEOUT,
)

# 延迟导入的符号
_lazy_symbols = {
    "Agent": "miniagent.agent.runtime:Agent",
    "AgentObserver": "miniagent.agent.runtime:AgentObserver",
    "AgentRequest": "miniagent.agent.runtime:AgentRequest",
    "AgentResult": "miniagent.agent.runtime:AgentResult",
    "AgentServices": "miniagent.agent.runtime:AgentServices",
    "AgentSettings": "miniagent.agent.runtime:AgentSettings",
    "AGENT_IDENTITY": "miniagent.agent.executor:AGENT_IDENTITY",
    "TaskDifficulty": "miniagent.agent.task_classifier:TaskDifficulty",
    "execute_plan": "miniagent.agent.executor:execute_plan",
    "generate_plan": "miniagent.agent.planner:generate_plan",
    "run_agent": "miniagent.agent.agent:run_agent",
    "run_pipeline": "miniagent.agent.agent:run_pipeline",
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
    "Agent",
    "AGENT_IDENTITY",
    "AGENT_NAME",
    "AgentObserver",
    "AgentRequest",
    "AgentResult",
    "AgentServices",
    "AgentSettings",
    "DEFAULT_AGENT_MAX_TURNS",
    "DEFAULT_AGENT_TOOL_TIMEOUT",
    "TaskDifficulty",
    "execute_plan",
    "generate_plan",
    "get_default_agent_config",
    "merge_agent_config",
    "run_agent",
    "run_pipeline",
]
