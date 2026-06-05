"""ClawHub 技能市场 HTTP 客户端（clawhub.ai API）。

提供：
- 技能搜索（关键词/标签）
- 技能详情查询
- 技能下载和安装
- 本地技能搜索（降级模式）

ClawHub API 约定：
- 基础 URL: https://clawhub.ai/api/v1
- 搜索: GET /v1/skills/search?q=<query>&limit=<n>
- 详情: GET /v1/skills/<slug>
- 下载: GET /v1/skills/<slug>/download?version=<ver>

联网失败时的降级行为见 ``search_local_skills``；合规使用见 ``workspaces/skills/THIRD_PARTY_SKILLS.md``。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol

_logger = logging.getLogger(__name__)

CLAWHUB_API = "https://clawhub.ai/api/v1"


def skill_install_dir_name(slug: str) -> str:
    """与 ``discover_skill_packages`` 对齐的包目录名（``skills_root`` 下一级）。

    ClawHub 可能使用 ``author/pkg`` 形式 slug；安装时写入 ``skills_root/<pkg>``，
    避免仅出现 ``skills_root/author`` 而无包级 ``SKILL.md`` 导致发现失败。
    """
    return slug.replace("\\", "/").rstrip("/").split("/")[-1]


# ─── 客户端接口 ──────────────────────────────────────────


class ClawHubClient(Protocol):
    """ClawHub 客户端接口。"""

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """按关键词搜索技能，返回 API 原始 item 列表。"""
        ...

    async def get_detail(self, slug: str) -> dict[str, Any]:
        """拉取指定 slug 的技能元数据与文件清单。"""
        ...

    async def download(
        self,
        slug: str,
        version: str | None = None,
        *,
        skills_root: str | None = None,
    ) -> dict[str, Any]:
        """下载并解压技能包到 ``skills_root``，返回结果摘要 dict。"""
        ...


# ─── 客户端实现 ──────────────────────────────────────────


# 网络可靠性：全局 HTTP 客户端复用（连接池）
_CLAWHUB_HTTP_CLIENT: Any = None  # httpx.AsyncClient | None


async def _get_clawhub_client(timeout: float = 15.0) -> Any:
    """获取全局 ClawHub HTTP 客户端（复用连接池）。"""
    global _CLAWHUB_HTTP_CLIENT
    if _CLAWHUB_HTTP_CLIENT is None:
        try:
            import httpx
            _CLAWHUB_HTTP_CLIENT = httpx.AsyncClient(timeout=timeout)
        except ImportError as e:
            _logger.debug("httpx未安装，回退到urllib: %s", e)
    return _CLAWHUB_HTTP_CLIENT


async def close_clawhub_client() -> None:
    """关闭全局 ClawHub HTTP 客户端（进程退出时调用）。"""
    global _CLAWHUB_HTTP_CLIENT
    if _CLAWHUB_HTTP_CLIENT is not None:
        try:
            await _CLAWHUB_HTTP_CLIENT.aclose()
        except Exception as e:
            _logger.debug("关闭HTTP客户端失败: %s", e)
        _CLAWHUB_HTTP_CLIENT = None


class _ClawHubClientImpl:
    """ClawHub 客户端实现。"""

    def __init__(self, base_url: str = CLAWHUB_API) -> None:
        """Args:
        base_url: API 根路径，默认 ``CLAWHUB_API``。
        """
        self._base_url = base_url

    async def _fetch_json(self, url: str) -> Any:
        """发起 HTTP GET 请求（带重试机制）。"""
        # 网络可靠性：优先使用 httpx + 重试
        try:
            import httpx

            from miniagent.infrastructure.http_retry import async_http_request_with_retry

            client = await _get_clawhub_client()
            if client is not None:
                resp = await async_http_request_with_retry(
                    client,
                    "GET",
                    url,
                    headers={"User-Agent": "mini-agent-clawhub/1.0"},
                    max_retries=3,
                    backoff_factor=1.0,
                )
                return resp.json()

            # 回退：无全局客户端时创建临时客户端（带重试）
            async with httpx.AsyncClient(timeout=15.0) as temp_client:
                resp = await async_http_request_with_retry(
                    temp_client,
                    "GET",
                    url,
                    headers={"User-Agent": "mini-agent-clawhub/1.0"},
                    max_retries=3,
                    backoff_factor=1.0,
                )
                return resp.json()

        except ImportError:
            # 回退到 urllib（无重试）
            import asyncio
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "mini-agent-clawhub/1.0"})
            resp = await asyncio.to_thread(urlopen, req, timeout=15)
            return json.loads(resp.read().decode("utf-8"))

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """搜索技能。

        ClawHub API 端点:
        - GET /api/v1/skills?query=<q>&limit=<n> → {items: [...], nextCursor: ...}
        - GET /api/v1/search?q=<q> → {results: [...]}
        """
        from urllib.parse import quote

        # 优先尝试 /api/v1/skills?query= （官方端点）
        url = f"{self._base_url}/skills?query={quote(query)}&limit={limit}"
        data = await self._fetch_json(url)
        # 响应格式: {items: [...], nextCursor: ...}
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        # 回退到 /api/v1/search
        url2 = f"{self._base_url}/search?q={quote(query)}&limit={limit}"
        data2 = await self._fetch_json(url2)
        if isinstance(data2, dict) and "results" in data2:
            return data2["results"]
        return []

    async def get_detail(self, slug: str) -> dict[str, Any]:
        """获取技能详情。"""
        url = f"{self._base_url}/skills/{slug}"
        return await self._fetch_json(url)

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
        from miniagent.skills.paths import get_skills_root as _default_skills_root

        detail = await self.get_detail(slug)
        files = detail.get("files") or []
        if not files and isinstance(detail.get("latestVersion"), dict):
            files = detail["latestVersion"].get("files") or []
        if not files:
            files = await self._files_from_download_endpoint(slug, version)

        root = skills_root if skills_root else _default_skills_root()
        os.makedirs(root, exist_ok=True)
        dir_name = skill_install_dir_name(slug)
        skills_dir = os.path.join(root, dir_name)
        os.makedirs(skills_dir, exist_ok=True)

        if not files:
            raise RuntimeError(
                f"ClawHub 未返回可写入的文件列表（slug={slug!r}）。"
                "请改用 GitHub 源：python scripts/vendor_skill_from_github.py …，"
                "或复制仓库内 workspaces/skills 已 vendoring 的技能包。"
            )

        # 写入文件
        for file_info in files:
            rel_path = file_info["path"]
            # 防止路径穿越：确保文件路径不包含 .. 且不跳出 skills_dir
            if ".." in rel_path or os.path.isabs(rel_path):
                raise RuntimeError(f"技能包文件路径不安全: {rel_path!r}")
            file_path = os.path.join(skills_dir, rel_path)
            # 二次校验：确保解析后路径仍在 skills_dir 内
            if not os.path.abspath(file_path).startswith(os.path.abspath(skills_dir)):
                raise RuntimeError(f"技能包文件路径跳出技能目录: {rel_path!r}")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            Path(file_path).write_text(file_info["content"], encoding="utf-8")

        # 写入 .clawhub 元数据
        from datetime import datetime, timezone

        meta_path = os.path.join(skills_dir, ".clawhub.json")
        Path(meta_path).write_text(
            json.dumps(
                {
                    "slug": detail.get("slug", slug),
                    "version": detail.get("version", "unknown"),
                    "installedAt": datetime.now(timezone.utc).isoformat(),
                    "source": "clawhub",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        return {"path": skills_dir, "files": files}


def create_clawhub_client(base_url: str = CLAWHUB_API) -> _ClawHubClientImpl:
    """创建 ClawHub 客户端。"""
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

    Args:
        skills_root: 技能根目录路径（主根）
        query: 搜索关键词（空字符串返回所有技能）
        extra_roots: 额外技能根目录列表（如会话技能目录），按顺序扫描

    Returns:
        匹配的本地技能列表（按根顺序去重，同 slug 以首次出现为准）
    """
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

            # 解析 front matter
            meta_match = re.match(r"^---\n([\s\S]*?)\n---", content)
            frontmatter = meta_match.group(1) if meta_match else ""

            name_match = re.search(r"name:\s*(.+)", frontmatter)
            name = name_match.group(1).strip() if name_match else entry

            desc_match = re.search(r"description:\s*(.+)", frontmatter)
            description = desc_match.group(1).strip() if desc_match else ""

            # 匹配
            if query_lower := query.lower():
                if not (
                    query_lower in name.lower()
                    or query_lower in description.lower()
                    or query_lower in content.lower()
                ):
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


__all__ = ["create_clawhub_client", "search_local_skills", "skill_install_dir_name"]
