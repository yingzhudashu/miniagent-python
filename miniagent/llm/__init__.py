"""Provider-neutral LLM contracts, catalog and routing gateway.

Importing :mod:`miniagent.llm` never constructs clients or imports optional
provider SDKs. Concrete adapters live under :mod:`miniagent.llm.providers`.
"""

from miniagent.llm.catalog import ModelCatalog, RoleRouter
from miniagent.llm.embeddings import (
    EmbeddingClient,
    EmbeddingConfig,
    EmbeddingRequest,
    EmbeddingResponse,
)
from miniagent.llm.gateway import LLMGateway, ProviderRegistry
from miniagent.llm.types import (
    LLMCompletion,
    LLMProvider,
    LLMRole,
    LLMStreamEvent,
    LLMTransportError,
    LLMUsage,
    ModelDescriptor,
    ProviderConfig,
)

__all__ = [
    "LLMCompletion",
    "EmbeddingClient",
    "EmbeddingConfig",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "LLMGateway",
    "LLMProvider",
    "LLMRole",
    "LLMStreamEvent",
    "LLMTransportError",
    "LLMUsage",
    "ModelCatalog",
    "ModelDescriptor",
    "ProviderConfig",
    "ProviderRegistry",
    "RoleRouter",
]
