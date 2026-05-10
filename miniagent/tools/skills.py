"""Mini Agent Python — 技能管理工具 (Phase 5)

提供技能搜索、安装、列表工具，让 Agent 能自主管理技能扩展。
- search_skills: 搜索 ClawHub 或本地技能
- install_skill: 下载并安装技能
- list_skills: 列出已安装技能
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.types.tool import ToolDefinition, ToolContext, ToolResult


def _get_skills_root() -> str:
    """与引擎、ClawHub 安装共用技能根目录（见 :func:`miniagent.skills.paths.get_skills_root`）。"""
    from miniagent.skills.paths import get_skills_root

    return get_skills_root()


# ════════════════════════════════════════════════════════
# search_skills
# ════════════════════════════════════════════════════════

_search_schema = {
    "type": "function",
    "function": {
        "name": "search_skills",
        "description": "搜索 ClawHub 技能市场或本地已安装的技能",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "source": {
                    "type": "string",
                    "enum": ["clawhub", "local", "all"],
                    "description": "搜索来源",
                },
                "limit": {"type": "number", "description": "最大返回结果数（默认 10）"},
            },
            "required": ["query"],
        },
    },
}


def _resolve_clawhub(ctx: ToolContext) -> Any:
    """优先使用注入的 ClawHub 客户端，否则按需新建。"""
    if ctx.clawhub is not None:
        return ctx.clawhub
    from miniagent.skills.clawhub_client import create_clawhub_client

    return create_clawhub_client()


async def _search_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """搜索 ClawHub 技能市场或本地已安装的技能。

    支持三种搜索来源：local（仅本地）、clawhub（仅在线）、all（同时搜索）。
    本地搜索通过匹配 SKILL.md 中的名称和描述实现，ClawHub 搜索调用远程 API。

    Args:
        args: 包含 query（搜索关键词）、source（可选，默认 'all'）、limit（可选，默认 10）
        ctx: 工具执行上下文（可选注入 clawhub）

    Returns:
        ToolResult: 搜索结果列表，包含名称、描述、星级和下载量
    """
    query = str(args["query"])
    source = str(args.get("source", "all"))
    limit = int(args.get("limit", 10))

    results: list[str] = [f"🔍 搜索技能: \"{query}\" (来源: {source})\n"]

    # 本地搜索
    if source in ("local", "all"):
        from miniagent.skills.clawhub_client import search_local_skills

        skills_root = _get_skills_root()
        local_results = search_local_skills(skills_root, query)
        if local_results:
            results.append("📁 本地技能:")
            for s in local_results[:limit]:
                results.append(f"  - [{s['slug']}] {s['name']}: {s['description']}")
            results.append("")
        elif source == "local":
            results.append("  未找到匹配的本地技能")

    # ClawHub 搜索
    if source in ("clawhub", "all"):
        try:
            client = _resolve_clawhub(ctx)
            clawhub_results = await client.search(query, limit)
            if clawhub_results:
                results.append("🌐 ClawHub 技能:")
                for s in clawhub_results:
                    results.append(
                        f"  - [{s['slug']}] {s['name']}: {s['description']} "
                        f"⭐{s.get('stars', 0)} ⬇{s.get('downloads', 0)}"
                    )
            elif source == "clawhub":
                results.append("  未找到匹配的在线技能")
        except Exception as e:
            results.append(f"⚠️ ClawHub 搜索失败: {e}")

    return ToolResult(success=True, content="\n".join(results) or "未找到任何结果")


# ════════════════════════════════════════════════════════
# install_skill
# ════════════════════════════════════════════════════════

_install_schema = {
    "type": "function",
    "function": {
        "name": "install_skill",
        "description": "从 ClawHub 技能市场下载并安装一个技能",
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "技能的 slug（唯一标识符）"},
                "version": {"type": "string", "description": "版本号（可选）"},
            },
            "required": ["slug"],
        },
    },
}


async def _install_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """从 ClawHub 技能市场下载并安装一个技能。

    此工具标记为 require-confirm 权限，安装前需用户确认。
    安装前检查是否已存在同名技能，防止覆盖。

    Args:
        args: 包含 slug（技能唯一标识）、version（可选，指定版本）
        ctx: 工具执行上下文（可选注入 clawhub）

    Returns:
        ToolResult: 成功时返回安装路径、版本号和文件数；失败时提示原因
    """
    slug = str(args["slug"])
    version = args.get("version")

    try:
        client = _resolve_clawhub(ctx)
        skills_root = _get_skills_root()
        install_dir = os.path.join(skills_root, slug)

        if os.path.exists(install_dir):
            return ToolResult(
                success=False,
                content=f"⚠️ 技能 \"{slug}\" 已安装在 {install_dir}\n如需重新安装，请先删除该目录",
            )

        result = await client.download(slug, version, skills_root=skills_root)
        detail = await client.get_detail(slug)

        return ToolResult(
            success=True,
            content=(
                f"✅ 技能 \"{slug}\" 安装成功！\n\n"
                f"📁 安装路径: {result['path']}\n"
                f"📦 版本: {detail.get('version', 'unknown')}\n"
                f"📄 文件数: {len(result.get('files', []))}\n\n"
                f"💡 提示：重启 Agent 后新技能将自动加载"
            ),
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f"❌ 安装技能 \"{slug}\" 失败: {e}\n\n请检查 slug 是否正确",
        )


# ════════════════════════════════════════════════════════
# list_skills
# ════════════════════════════════════════════════════════

_list_schema = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": "列出所有已安装的本地技能",
        "parameters": {
            "type": "object",
            "properties": {
                "verbose": {"type": "boolean", "description": "是否显示详细信息"},
            },
        },
    },
}


async def _list_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """列出所有已安装的本地技能。

    通过扫描 skills/ 目录下的 SKILL.md 文件实现。
    verbose 模式下额外显示版本、作者和安装路径信息。

    Args:
        args: 包含 verbose（可选，是否显示详细信息）
        _ctx: 工具执行上下文（此工具不使用）

    Returns:
        ToolResult: 已安装技能列表，或提示使用 search_skills 安装新技能
    """
    verbose = bool(args.get("verbose", False))
    skills_root = _get_skills_root()

    from miniagent.skills.clawhub_client import search_local_skills

    results = search_local_skills(skills_root, "")

    if not results:
        return ToolResult(
            success=True,
            content="📦 暂无已安装的技能\n\n使用 search_skills 工具搜索并安装新技能",
        )

    lines = ["📦 已安装技能:\n"]
    for s in results:
        lines.append(f"  - [{s['slug']}] {s['name']}")
        lines.append(f"    {s['description']}")
        if verbose:
            lines.append(f"    版本: {s.get('version', 'local')} | 作者: {s.get('author', 'local')}")
            lines.append(f"    路径: {os.path.join(skills_root, s['slug'])}")
        lines.append("")

    return ToolResult(success=True, content="\n".join(lines))


# ─── 导出 ────────────────────────────────────────────────

skills_tools: dict[str, ToolDefinition] = {
    "search_skills": ToolDefinition(
        schema=_search_schema,
        handler=_search_handler,
        permission="sandbox",
        help_text="搜索技能市场",
        toolbox="skills_management",
    ),
    "install_skill": ToolDefinition(
        schema=_install_schema,
        handler=_install_handler,
        permission="require-confirm",
        help_text="安装技能",
        toolbox="skills_management",
    ),
    "list_skills": ToolDefinition(
        schema=_list_schema,
        handler=_list_handler,
        permission="sandbox",
        help_text="列出已安装技能",
        toolbox="skills_management",
    ),
}

__all__ = ["skills_tools"]
