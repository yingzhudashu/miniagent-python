"""Mini Agent Python — ClawHub 技能市场客户端 (Phase 6)

参考 OpenClaw 的 ClawHub (clawhub.ai) 设计，提供：
- 技能搜索（关键词/标签）
- 技能详情查询
- 技能下载和安装
- 本地技能搜索（降级模式）

ClawHub API 约定：
- 基础 URL: https://clawhub.ai/api/v1
- 搜索: GET /v1/skills/search?q=<query>&limit=<n>
- 详情: GET /v1/skills/<slug>
- 下载: GET /v1/skills/<slug>/download?version=<ver>
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

CLAWHUB_API = "https://clawhub.ai/api/v1"


# ─── 客户端接口 ──────────────────────────────────────────

class ClawHubClient(Protocol):
    """ClawHub 客户端接口。"""

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        ...

    async def get_detail(self, slug: str) -> dict[str, Any]:
        ...

    async def download(self, slug: str, version: str | None = None) -> dict[str, Any]:
        ...


# ─── 客户端实现 ──────────────────────────────────────────

class _ClawHubClientImpl:
    """ClawHub 客户端实现。"""

    def __init__(self, base_url: str = CLAWHUB_API) -> None:
        self._base_url = base_url

    async def _fetch_json(self, url: str) -> Any:
        """发起 HTTP GET 请求。"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "mini-agent-clawhub/1.0"},
                )
                resp.raise_for_status()
                return resp.json()
        except ImportError:
            # 回退到 urllib
            import asyncio
            from urllib.request import Request, urlopen

            req = Request(url, headers={"User-Agent": "mini-agent-clawhub/1.0"})
            resp = await asyncio.to_thread(urlopen, req, timeout=15)
            return json.loads(resp.read().decode("utf-8"))

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """搜索技能。"""
        from urllib.parse import quote

        url = f"{self._base_url}/skills/search?q={quote(query)}&limit={limit}"
        return await self._fetch_json(url)

    async def get_detail(self, slug: str) -> dict[str, Any]:
        """获取技能详情。"""
        url = f"{self._base_url}/skills/{slug}"
        return await self._fetch_json(url)

    async def download(self, slug: str, version: str | None = None) -> dict[str, Any]:
        """下载技能包并安装到本地。"""
        detail = await self.get_detail(slug)
        files = detail.get("files", [])

        # 确定本地路径
        project_root = _find_project_root()
        skills_dir = os.path.join(project_root, "skills", slug)

        # 写入文件
        for file_info in files:
            file_path = os.path.join(skills_dir, file_info["path"])
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


# ─── 项目根目录查找 ──────────────────────────────────────

def _find_project_root() -> str:
    """从 cwd 向上查找项目根目录。"""
    d = Path.cwd()
    while d != d.parent:
        if (d / "pyproject.toml").exists() or (d / "setup.py").exists():
            return str(d)
        d = d.parent
    return str(Path.cwd())


# ─── 本地技能搜索 ────────────────────────────────────────

def search_local_skills(skills_root: str, query: str) -> list[dict[str, Any]]:
    """本地技能搜索（不依赖网络）。

    读取每个技能目录下的 SKILL.md，匹配名称、描述和内容。

    Args:
        skills_root: 技能根目录路径
        query: 搜索关键词（空字符串返回所有技能）

    Returns:
        匹配的本地技能列表
    """
    if not os.path.isdir(skills_root):
        return []

    results: list[dict[str, Any]] = []
    query_lower = query.lower()

    for entry in sorted(os.listdir(skills_root)):
        if entry.startswith("."):
            continue
        skill_dir = os.path.join(skills_root, entry)
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
        if query_lower and not (
            query_lower in name.lower()
            or query_lower in description.lower()
            or query_lower in content.lower()
        ):
            continue

        results.append({
            "slug": entry,
            "name": name,
            "description": description,
            "version": "local",
            "tags": [],
            "downloads": 0,
            "stars": 0,
            "author": "local",
        })

    return results


__all__ = ["create_clawhub_client", "search_local_skills"]
