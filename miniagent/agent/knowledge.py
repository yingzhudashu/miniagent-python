"""Knowledge retrieval policy over the injected registry port."""

from __future__ import annotations

from miniagent.agent.logging import get_logger
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.settings import get_config

_logger = get_logger(__name__)


def retrieve_knowledge_context(
    registry: KnowledgeRegistryProtocol,
    query: str,
    phase: str = "executor",
    default_top_k: int = 3,
    default_max_chars: int = 4000,
) -> str:
    if not get_config(f"knowledge.{phase}_enabled", True):
        return ""
    try:
        if not registry.list():
            return ""
        top_k = get_config(f"knowledge.{phase}_top_k", default_top_k)
        max_chars = get_config(f"knowledge.{phase}_max_chars", default_max_chars)
        result = registry.search(query, top_k=top_k, max_chars=max_chars)
        if result:
            _logger.debug("%s knowledge retrieval: %d chars", phase, len(result))
            return f"\n\n## 相关知识库摘要\n\n{result}"
    except Exception as error:
        _logger.debug("%s knowledge retrieval failed (non-critical): %s", phase, error)
    return ""


__all__ = ["retrieve_knowledge_context"]
