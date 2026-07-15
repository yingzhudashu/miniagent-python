"""ClawHub 技能市场 HTTP 客户端（clawhub.ai API）。

提供：
- 技能搜索（关键词/标签）
- 技能详情查询
- 技能下载和安装
- 本地技能搜索（降级模式）

ClawHub API 约定（实现会按顺序尝试兼容端点）：
- 基础 URL: https://clawhub.ai/api/v1
- 搜索: GET /skills?query=<q>&limit=<n>，失败时回退 GET /search?q=<q>
- 详情: GET /skills/<slug>
- 下载: GET /skills/<slug>/download?version=<ver>（JSON 文件列表）

联网失败时的降级行为见 ``search_local_skills``；合规使用见 ``workspaces/skills/THIRD_PARTY_SKILLS.md``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from miniagent.agent.constants import CLAWHUB_API_URL
from miniagent.agent.types.skill import (
    ClawHubClientProtocol,
    ClawHubSearchResult,
    ClawHubSkillDetail,
)
from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json, atomic_write_text

_logger = logging.getLogger(__name__)

CLAWHUB_API = CLAWHUB_API_URL


def _clawhub_base_url() -> str:
    return CLAWHUB_API_URL


def skill_install_dir_name(slug: str) -> str:
    """与 ``discover_skill_packages`` 对齐的包目录名（``skills_root`` 下一级）。

    ClawHub 可能使用 ``author/pkg`` 形式 slug；安装时写入 ``skills_root/<pkg>``，
    避免仅出现 ``skills_root/author`` 而无包级 ``SKILL.md`` 导致发现失败。
    """
    return slug.replace("\\", "/").rstrip("/").split("/")[-1]


def _to_search_result(item: dict[str, Any]) -> ClawHubSearchResult:
    """将 ClawHub API item 映射为 ``ClawHubSearchResult``。"""
    version = str(item.get("version") or "")
    latest = item.get("latestVersion")
    if not version and isinstance(latest, dict):
        version = str(latest.get("version") or "")
    return ClawHubSearchResult(
        slug=str(item.get("slug") or ""),
        name=str(item.get("name") or ""),
        description=str(item.get("description") or ""),
        version=version,
        tags=[str(t) for t in (item.get("tags") or [])],
        downloads=int(item.get("downloads") or 0),
        stars=int(item.get("stars") or 0),
        author=str(item.get("author") or ""),
    )


def _extract_files(data: dict[str, Any]) -> list[dict[str, str]]:
    """从详情响应中提取文件列表。"""
    files: list[dict[str, str]] = []
    latest = data.get("latestVersion")
    if isinstance(latest, dict):
        raw = latest.get("files") or []
        if isinstance(raw, list):
            files = [f for f in raw if isinstance(f, dict)]
    if not files:
        raw = data.get("files") or []
        if isinstance(raw, list):
            files = [f for f in raw if isinstance(f, dict)]
    return files


def _to_skill_detail(data: dict[str, Any], slug: str) -> ClawHubSkillDetail:
    """将 ClawHub API 详情映射为 ``ClawHubSkillDetail``。"""
    skill_md = ""
    latest = data.get("latestVersion")
    if isinstance(latest, dict):
        skill_md = str(latest.get("skillMd") or latest.get("skill_md") or "")
    if not skill_md:
        skill_md = str(data.get("skillMd") or data.get("skill_md") or "")
    version = str(data.get("version") or "")
    if not version and isinstance(latest, dict):
        version = str(latest.get("version") or "")
    return ClawHubSkillDetail(
        slug=str(data.get("slug") or slug),
        name=str(data.get("name") or ""),
        description=str(data.get("description") or ""),
        version=version,
        tags=[str(t) for t in (data.get("tags") or [])],
        skill_md=skill_md,
        files=_extract_files(data),
    )


# ─── 客户端实现 ──────────────────────────────────────────


class _ClawHubClientImpl:
    """ClawHub 客户端实现（符合 ``ClawHubClientProtocol``）。"""

    def __init__(self, base_url: str = CLAWHUB_API) -> None:
        """Args:
        base_url: API 根路径，默认 ``CLAWHUB_API``。
        """
        self._base_url = base_url
        self._http_client: Any = None

    async def _get_http_client(self, timeout: float = 15.0) -> Any:
        """Lazily create the connection pool owned by this client instance."""
        if self._http_client is None:
            try:
                import httpx

                self._http_client = httpx.AsyncClient(timeout=timeout)
            except ImportError as error:
                _logger.debug("httpx未安装，回退到urllib: %s", error)
        return self._http_client

    async def close(self) -> None:
        """Close the instance-owned HTTP pool; repeated calls are harmless."""
        client = self._http_client
        self._http_client = None
        if client is not None:
            await client.aclose()

    async def _fetch_json(self, url: str) -> Any:
        """发起 HTTP GET 请求（带重试机制）。"""
        # 网络可靠性：优先使用 httpx + 重试
        try:
            import httpx

            from miniagent.assistant.infrastructure.http_retry import async_http_request_with_retry

            async def _request(client: Any) -> Any:
                resp = await async_http_request_with_retry(
                    client,
                    "GET",
                    url,
                    headers={"User-Agent": "mini-agent-clawhub/1.0"},
                    max_retries=3,
                    backoff_factor=1.0,
                )
                return resp.json()

            client = await self._get_http_client()
            if client is not None:
                return await _request(client)

            # 极端降级：实例池创建失败时使用短生命周期客户端。
            async with httpx.AsyncClient(timeout=15.0) as temp_client:
                return await _request(temp_client)

        except ImportError:
            # 回退到 urllib（无重试）
            import asyncio
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "mini-agent-clawhub/1.0"})
            resp = await asyncio.to_thread(urlopen, req, timeout=15)
            return json.loads(resp.read().decode("utf-8"))

    async def search(self, query: str, limit: int = 10) -> list[ClawHubSearchResult]:
        """搜索技能。

        ClawHub API 端点:
        - GET /api/v1/skills?query=<q>&limit=<n> → {items: [...], nextCursor: ...}
        - GET /api/v1/search?q=<q> → {results: [...]}
        """
        from urllib.parse import quote

        url = f"{self._base_url}/skills?query={quote(query)}&limit={limit}"
        data = await self._fetch_json(url)
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            if isinstance(items, list):
                return [_to_search_result(i) for i in items if isinstance(i, dict)]
        url2 = f"{self._base_url}/search?q={quote(query)}&limit={limit}"
        data2 = await self._fetch_json(url2)
        if isinstance(data2, dict) and "results" in data2:
            results = data2["results"]
            if isinstance(results, list):
                return [_to_search_result(i) for i in results if isinstance(i, dict)]
        return []

    async def get_detail(self, slug: str) -> ClawHubSkillDetail:
        """获取技能详情。"""
        url = f"{self._base_url}/skills/{slug}"
        data = await self._fetch_json(url)
        if not isinstance(data, dict):
            return ClawHubSkillDetail(slug=slug, name="", description="", version="")
        return _to_skill_detail(data, slug)

    async def _files_from_download_endpoint(
        self, slug: str, version: str | None
    ) -> list[dict[str, Any]]:
        """部分部署在详情无 ``files`` 时由 ``GET .../download`` 返回 JSON 文件列表。"""
        from urllib.parse import quote

        url = f"{self._base_url}/skills/{slug}/download"
        if version:
            url += f"?version={quote(version)}"
        try:
            data = await self._fetch_json(url)
        except Exception:
            return []
        if isinstance(data, dict):
            raw = data.get("files") or []
            if isinstance(raw, list):
                return raw
        return []

    async def download(
        self,
        slug: str,
        version: str | None = None,
        *,
        skills_root: str | None = None,
    ) -> dict[str, Any]:
        """下载技能包并安装到本地 ``skills_root/<包目录名>``。

        ``包目录名`` 为 ``slug`` 路径的最后一段（与 :func:`skill_install_dir_name` 一致），
        以便 ``discover_skill_packages`` 能发现带 ``author/`` 前缀的 ClawHub slug。
        """
        from miniagent.assistant.skills.paths import get_skills_root as _default_skills_root

        detail = await self.get_detail(slug)
        files = list(detail.files)
        if not files:
            files = await self._files_from_download_endpoint(slug, version)

        root = skills_root if skills_root else _default_skills_root()
        dir_name = skill_install_dir_name(slug)
        skills_dir = os.path.join(root, dir_name)

        if not files:
            raise RuntimeError(
                f"ClawHub 未返回可写入的文件列表（slug={slug!r}）。"
                "请改用 GitHub 源：python scripts/vendor_skill_from_github.py …，"
                "或复制仓库内 workspaces/skills 已 vendoring 的技能包。"
            )

        from datetime import datetime, timezone

        def _install_sync() -> None:
            os.makedirs(skills_dir, exist_ok=True)
            root_abs = os.path.abspath(skills_dir)
            for file_info in files:
                rel_path = str(file_info["path"])
                if ".." in Path(rel_path).parts or os.path.isabs(rel_path):
                    raise RuntimeError(f"技能包文件路径不安全: {rel_path!r}")
                file_path = os.path.abspath(os.path.join(skills_dir, rel_path))
                try:
                    if os.path.commonpath([root_abs, file_path]) != root_abs:
                        raise RuntimeError(f"技能包文件路径跳出技能目录: {rel_path!r}")
                except ValueError as error:
                    raise RuntimeError(f"技能包文件路径跳出技能目录: {rel_path!r}") from error
                atomic_write_text(file_path, str(file_info["content"]), encoding="utf-8")

            atomic_dump_json(
                os.path.join(skills_dir, ".clawhub.json"),
                {
                    "slug": detail.slug,
                    "version": detail.version or "unknown",
                    "installedAt": datetime.now(timezone.utc).isoformat(),
                    "source": "clawhub",
                },
                indent=2,
            )

        await asyncio.to_thread(_install_sync)

        return {"path": skills_dir, "files": files}


def create_clawhub_client(base_url: str | None = None) -> ClawHubClientProtocol:
    """创建 ClawHub 客户端。"""
    if base_url is None:
        base_url = _clawhub_base_url()
    return _ClawHubClientImpl(base_url)


# ─── 本地技能搜索 ────────────────────────────────────────


def search_local_skills(
    skills_root: str,
    query: str,
    *,
    extra_roots: list[str] | None = None,
) -> list[dict[str, Any]]:
    """本地技能搜索（不依赖网络）。

    读取每个技能目录下的 SKILL.md，匹配名称、描述和内容。
    使用 ``parse_skill_md`` 解析 front matter（支持多行 description）。

    Args:
        skills_root: 技能根目录路径（主根）
        query: 搜索关键词（空字符串返回所有技能）
        extra_roots: 额外技能根目录列表（如会话技能目录），按顺序扫描

    Returns:
        匹配的本地技能列表（按根顺序去重，同 slug 以首次出现为准）
    """
    from miniagent.assistant.skills.loader import _description_from_meta, parse_skill_md

    all_roots = [skills_root]
    if extra_roots:
        all_roots.extend(extra_roots)

    seen_slugs: set[str] = set()
    results: list[dict[str, Any]] = []

    for root in all_roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if entry.startswith(".") or entry in seen_slugs:
                continue
            skill_dir = os.path.join(root, entry)
            if not os.path.isdir(skill_dir):
                continue

            skill_md_path = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_md_path):
                continue

            content = Path(skill_md_path).read_text(encoding="utf-8")
            meta, body = parse_skill_md(content)
            name = str(meta.get("name") or entry)
            description = _description_from_meta(meta, body)

            if query_lower := query.lower():
                haystack = "\n".join([name, description, body, content]).lower()
                if query_lower not in haystack:
                    continue

            seen_slugs.add(entry)
            results.append(
                {
                    "slug": entry,
                    "name": name,
                    "description": description,
                    "version": "local",
                    "tags": [],
                    "downloads": 0,
                    "stars": 0,
                    "author": "local",
                }
            )

    return results


__all__ = [
    "create_clawhub_client",
    "search_local_skills",
    "skill_install_dir_name",
]
