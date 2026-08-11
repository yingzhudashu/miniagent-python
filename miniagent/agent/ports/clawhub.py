"""ClawHub marketplace port and transport-neutral result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ClawHubSearchResult:
    """Transport-neutral marketplace search result."""
    slug: str
    name: str
    description: str
    version: str
    tags: list[str] = field(default_factory=list)
    downloads: int = 0
    stars: int = 0
    author: str = ""


@dataclass
class ClawHubSkillDetail:
    """Current skill manifest and downloadable file metadata."""
    slug: str
    name: str
    description: str
    version: str
    tags: list[str] = field(default_factory=list)
    skill_md: str = ""
    files: list[dict[str, str]] = field(default_factory=list)


@runtime_checkable
class ClawHubClientProtocol(Protocol):
    """Async marketplace boundary implemented by Assistant infrastructure."""
    async def search(self, query: str, limit: int = 10) -> list[ClawHubSearchResult]: ...

    async def get_detail(self, slug: str) -> ClawHubSkillDetail: ...

    async def download(
        self,
        slug: str,
        version: str | None = None,
        *,
        skills_root: str | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


__all__ = ["ClawHubClientProtocol", "ClawHubSearchResult", "ClawHubSkillDetail"]
