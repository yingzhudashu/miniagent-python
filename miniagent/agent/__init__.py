"""高质量问答 Agent 核心。

职责边界：
- **本包**：分类、澄清、规划、ReAct 执行、反思，以及生命周期、工具、Memory/RAG、Trace 与扩展契约。
- **公开入口**：``AgentRuntime(AgentSpec, llm, extensions)`` 与 ``AgentRuntime.run(AgentRequest)``。
- **非本包**：CLI/TUI/飞书输入展示在 ``miniagent.ui``；实例装配与进程信号在 ``miniagent.assistant``。

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
    "AgentRequest": "miniagent.agent.runtime:AgentRequest",
    "AgentResult": "miniagent.agent.runtime:AgentResult",
    "AgentRuntime": "miniagent.agent.runtime:AgentRuntime",
    "AgentSpec": "miniagent.agent.runtime:AgentSpec",
    "AgentSettings": "miniagent.agent.runtime:AgentSettings",
    "AgentEvent": "miniagent.agent.events:AgentEvent",
    "AgentEventKind": "miniagent.agent.events:AgentEventKind",
    "AgentExtension": "miniagent.agent.extensions:AgentExtension",
    "HybridRAGExtension": "miniagent.agent.rag:HybridRAGExtension",
    "JsonlTraceExporter": "miniagent.agent.tracing:JsonlTraceExporter",
    "RAGDocument": "miniagent.agent.rag:RAGDocument",
    "RetrievedDocument": "miniagent.agent.rag:RetrievedDocument",
    "TaskDifficulty": "miniagent.agent.task_classifier:TaskDifficulty",
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
    "AGENT_NAME",
    "AgentRequest",
    "AgentResult",
    "AgentRuntime",
    "AgentSpec",
    "AgentSettings",
    "AgentEvent",
    "AgentEventKind",
    "AgentExtension",
    "HybridRAGExtension",
    "JsonlTraceExporter",
    "RAGDocument",
    "RetrievedDocument",
    "DEFAULT_AGENT_MAX_TURNS",
    "DEFAULT_AGENT_TOOL_TIMEOUT",
    "TaskDifficulty",
    "get_default_agent_config",
    "merge_agent_config",
]
