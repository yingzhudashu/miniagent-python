"""Agent-owned optional retrieval-augmented generation infrastructure."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from miniagent.agent.lifecycle import HealthReport, HealthState
from miniagent.llm.embeddings import EmbeddingClient

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class RAGDocument:
    """Immutable local document stored and retrieved by the RAG extension."""

    document_id: str
    text: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.document_id.strip() or not self.text.strip():
            raise ValueError("RAG document id and text must not be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    """A ranked document with its combined and component relevance scores."""

    document: RAGDocument
    score: float
    keyword_score: float = 0.0
    vector_score: float = 0.0


@runtime_checkable
class Retriever(Protocol):
    """Minimal asynchronous retrieval contract consumed by Agent extensions."""

    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> Sequence[RetrievedDocument]: ...


class HybridRAGExtension:
    """Small local keyword/vector retriever with fail-open embedding policy."""

    extension_id = "rag"
    name = "rag"

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient | None = None,
        state_path: str | Path | None = None,
        enabled: bool = True,
        vector_enabled: bool = True,
        fail_open: bool = True,
        top_k: int = 8,
        min_score: float = 0.0,
    ) -> None:
        if top_k < 1:
            raise ValueError("RAG top_k must be at least 1")
        self.embedding_client = embedding_client
        self.state_path = Path(state_path) if state_path is not None else None
        self.enabled = enabled
        self.vector_enabled = vector_enabled
        self.fail_open = fail_open
        self.top_k = top_k
        self.min_score = min_score
        self._documents: dict[str, RAGDocument] = {}
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._state = HealthState.STOPPED
        self._detail = ""
        self._dirty = False

    async def initialize(self) -> None:
        """Validate vector dependencies and restore persisted local state."""
        self._state = HealthState.STARTING
        if self.enabled and self.vector_enabled and self.embedding_client is None:
            raise ValueError("vector RAG is enabled but no EmbeddingClient was provided")
        self._load()

    async def start(self) -> None:
        """Expose the configured enabled/disabled health state."""
        self._state = HealthState.READY if self.enabled else HealthState.STOPPED

    async def stop(self) -> None:
        """Persist dirty state and close the extension-owned embedding client."""
        self._save()
        if self.embedding_client is not None:
            await self.embedding_client.close()
        self._state = HealthState.STOPPED

    def health(self) -> HealthReport:
        """Return readiness and current document/vector counts."""
        return HealthReport(
            self._state,
            self._detail,
            {"documents": len(self._documents), "vectors": len(self._vectors)},
        )

    async def add(self, document: RAGDocument) -> None:
        """Insert or replace a document and refresh its vector when enabled."""
        self._documents[document.document_id] = document
        # Replacing text must never retain the previous text's vector if embedding
        # is disabled or the new embedding request fails open.
        self._vectors.pop(document.document_id, None)
        self._dirty = True
        if self.enabled and self.vector_enabled:
            await self._embed_document(document)

    def remove(self, document_id: str) -> bool:
        """Remove a document and any vector, returning whether it existed."""
        existed = self._documents.pop(document_id, None) is not None
        self._vectors.pop(document_id, None)
        self._dirty = self._dirty or existed
        return existed

    async def retrieve(
        self, query: str, *, top_k: int | None = None
    ) -> tuple[RetrievedDocument, ...]:
        """Rank documents using keyword/vector scores with fail-open embeddings."""
        clean = query.strip()
        if not self.enabled or not clean or not self._documents:
            return ()
        keyword = self._keyword_scores(clean)
        vector: dict[str, float] = {}
        if self.vector_enabled and self.embedding_client is not None:
            try:
                query_vector = await self.embedding_client.embed(clean)
                vector = {
                    key: self._cosine(query_vector, value)
                    for key, value in self._vectors.items()
                }
                self._recover_health()
            except Exception as error:
                self._state = HealthState.DEGRADED
                self._detail = f"embedding retrieval degraded: {type(error).__name__}"
                if not self.fail_open:
                    raise
        results = self._merge_scores(keyword, vector)
        limit = top_k or self.top_k
        return tuple(result for result in results if result.score >= self.min_score)[:limit]

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Synchronous keyword-only adapter for the current Agent knowledge port."""
        del kb_name
        ranked = self._merge_scores(self._keyword_scores(query), {})[: top_k or self.top_k]
        text = "\n\n".join(item.document.text for item in ranked)
        return text[:max_chars] if max_chars is not None else text

    async def _embed_document(self, document: RAGDocument) -> None:
        if self.embedding_client is None:
            return
        try:
            value = await self.embedding_client.embed(document.text)
            self._vectors[document.document_id] = tuple(value)
            self._recover_health()
        except Exception as error:
            self._state = HealthState.DEGRADED
            self._detail = f"embedding indexing degraded: {type(error).__name__}"
            if not self.fail_open:
                raise

    def _keyword_scores(self, query: str) -> dict[str, float]:
        query_tokens = set(_TOKEN_RE.findall(query.lower()))
        if not query_tokens:
            return {}
        scores: dict[str, float] = {}
        for key, document in self._documents.items():
            tokens = set(_TOKEN_RE.findall(document.text.lower()))
            scores[key] = len(query_tokens & tokens) / len(query_tokens)
        return scores

    def _merge_scores(
        self,
        keyword: dict[str, float],
        vector: dict[str, float],
    ) -> list[RetrievedDocument]:
        results = [
            RetrievedDocument(
                document,
                keyword.get(key, 0.0) * 0.4 + max(0.0, vector.get(key, 0.0)) * 0.6,
                keyword.get(key, 0.0),
                vector.get(key, 0.0),
            )
            for key, document in self._documents.items()
        ]
        results.sort(key=lambda item: (-item.score, item.document.document_id))
        return results

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0

    def _recover_health(self) -> None:
        if self._state is HealthState.DEGRADED:
            self._state = HealthState.READY
            self._detail = ""

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        for item in payload.get("documents", []):
            document = RAGDocument(
                str(item["document_id"]), str(item["text"]), dict(item.get("metadata", {}))
            )
            self._documents[document.document_id] = document
        self._vectors = {
            str(key): tuple(float(value) for value in vector)
            for key, vector in payload.get("vectors", {}).items()
        }

    def _save(self) -> None:
        if self.state_path is None or not self._dirty:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "documents": [
                {
                    "document_id": item.document_id,
                    "text": item.text,
                    "metadata": dict(item.metadata),
                }
                for item in self._documents.values()
            ],
            "vectors": self._vectors,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        handle, temporary = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
            dir=self.state_path.parent,
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        self._dirty = False


__all__ = [
    "HybridRAGExtension",
    "RAGDocument",
    "RetrievedDocument",
    "Retriever",
]
