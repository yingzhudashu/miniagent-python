"""Application-facing contract for the mounted knowledge-base registry."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class KnowledgeRegistryProtocol(Protocol):
    """Process-owned knowledge registry injected by the composition root."""

    def list(self) -> list[dict[str, Any]]: ...

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
    ) -> str: ...

    def mount(self, path: str, name: str | None = None) -> dict[str, Any]: ...

    def unmount(self, name: str) -> dict[str, Any]: ...

    def reload(self, name: str | None = None) -> dict[str, Any]: ...

    def get_kb(self, name: str) -> Any | None: ...

    def refresh_auto_file_kb(self, path: str, name: str) -> Any: ...


__all__ = ["KnowledgeRegistryProtocol"]
