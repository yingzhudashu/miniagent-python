"""Mini Agent Python — 技能管理工具

提供技能搜索、安装、列表、卸载工具，让 Agent 能自主管理技能扩展。
- search_skills: 搜索 ClawHub 或本地技能
- install_skill: 从 ClawHub 下载并安装技能
- uninstall_skill: 卸载已安装技能
- list_skills: 列出已安装技能
- check_app_availability: 检查依赖可用性（从 web.py 合入）

技能目录与第三方许可见 ``workspaces/skills/THIRD_PARTY_SKILLS.md``；市场 API 见 ``clawhub_client``。

重构说明：
- 使用 ToolBuilder 简化工具定义
- 合入 check_app_availability 工具（原 web.py）
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from miniagent.tools.base import tool
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = logging.getLogger(__name__)


def _clawhub_field(item: Any, key: str, default: Any = "") -> Any:
    """从 ClawHub 搜索结果（dataclass 或 dict）读取字段。"""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _get_skills_root() -> str:
    """与引擎、ClawHub 安装共用技能根目录。"""
    from miniagent.skills.paths import get_skills_root
    return get_skills_root()


def _resolve_clawhub(ctx: ToolContext) -> Any:
    """优先使用注入的 ClawHub 客户端，否则按需新建。"""
    if ctx.clawhub is not None:
        return ctx.clawhub
    from miniagent.skills.clawhub_client import create_clawhub_client
    return create_clawhub_client()


# ════════════════════════════════════════════════════════
# Skills Handlers
# ════════════════════════════════════════════════════════


async def _search_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """搜索 ClawHub 技能市场或本地已安装的技能。"""
    query = str(args["query"])
    source = str(args.get("source", "all"))
    limit = int(args.get("limit", 10))

    results: list[str] = [f'🔍 搜索技能: "{query}" (来源: {source})\n']

    # 本地搜索
    if source in ("local", "all"):
        from miniagent.skills.clawhub_client import search_local_skills
        from miniagent.skills.paths import get_all_skill_roots, get_skills_root

        skills_root = get_skills_root()
        extra_roots = [r for r in get_all_skill_roots() if r != skills_root]
        local_results = search_local_skills(skills_root, query, extra_roots=extra_roots)
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
                    slug = _clawhub_field(s, "slug")
                    name = _clawhub_field(s, "name")
                    desc = _clawhub_field(s, "description")
                    stars = _clawhub_field(s, "stars", 0)
                    downloads = _clawhub_field(s, "downloads", 0)
                    results.append(
                        f"  - [{slug}] {name}: {desc} "
                        f"⭐{stars} ⬇{downloads}"
                    )
            elif source == "clawhub":
                results.append("  未找到匹配的在线技能")
        except Exception as e:
            results.append(f"{WARNING_PREFIX} ClawHub 搜索失败: {e}")

    return ToolResult(success=True, content="\n".join(results) or "未找到任何结果")


async def _install_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """从 ClawHub 技能市场下载并安装一个技能。"""
    slug = str(args["slug"])
    version = args.get("version")

    try:
        client = _resolve_clawhub(ctx)
        skills_root = _get_skills_root()
        from miniagent.skills.clawhub_client import skill_install_dir_name

        dir_name = skill_install_dir_name(slug)
        install_dir = os.path.join(skills_root, dir_name)

        if os.path.exists(install_dir):
            return ToolResult(
                success=False,
                content=f'{WARNING_PREFIX} 技能 "{slug}" 已安装在 {install_dir}\n如需重新安装，请先删除该目录',
            )

        result = await client.download(slug, version, skills_root=skills_root)
        detail = await client.get_detail(slug)

        # 自动审查新安装的技能
        install_path = result.get("path") or install_dir
        vet_report = ""
        if os.path.isdir(install_path):
            from miniagent.skills.autovet import auto_vet_skill
            vet_report = auto_vet_skill(install_path)

        st = ctx.cli_loop_state
        rt = st.get("runtime_ctx") if isinstance(st, dict) else None
        if rt is not None:
            from miniagent.skills.refresh import refresh_skills
            try:
                fr = await refresh_skills(
                    rt.registry, rt.skill_registry, package_dir=install_path,
                    state=st, session_manager=st.get("session_manager"),
                )
                refresh_note = f"\n\n🔄 已热加载到当前 Agent（{len(fr.loaded_skills)} 个技能，新增工具 {len(fr.added_tools)} 个）"
            except Exception as ex:
                refresh_note = f"\n\n{WARNING_PREFIX} 安装成功但热加载失败: {ex}\n请执行 `.reload-skills` 或重启 Agent"
        else:
            refresh_note = "\n\n💡 提示：执行 `.reload-skills` 或重启 Agent 后加载"

        return ToolResult(
            success=True,
            content=(
                f'{SUCCESS_PREFIX} 技能 "{slug}" 安装成功！\n\n'
                f"📁 安装路径: {install_path}\n"
                f"📦 版本: {detail.version or 'unknown'}\n"
                f"📄 文件数: {len(result.get('files', []))}"
                f"{vet_report}{refresh_note}"
            ),
        )
    except Exception as e:
        return ToolResult(
            success=False,
            content=f'{ERROR_PREFIX} 安装技能 "{slug}" 失败: {e}\n\n请检查 slug 是否正确',
        )


async def _list_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """列出所有已安装的本地技能。"""
    verbose = bool(args.get("verbose", False))
    from miniagent.skills.clawhub_client import search_local_skills
    from miniagent.skills.paths import get_all_skill_roots, get_skills_root

    skills_root = get_skills_root()
    extra_roots = [r for r in get_all_skill_roots() if r != skills_root]
    results = search_local_skills(skills_root, "", extra_roots=extra_roots)

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


async def _uninstall_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """卸载一个已安装的技能，包括删除磁盘目录和热移除工具。"""
    slug = str(args["slug"])

    try:
        skills_root = _get_skills_root()
        from miniagent.skills.clawhub_client import skill_install_dir_name

        dir_name = skill_install_dir_name(slug)
        install_dir = os.path.join(skills_root, dir_name)

        if not os.path.isdir(install_dir):
            return ToolResult(
                success=False,
                content=f'{WARNING_PREFIX} 技能 "{slug}" 未安装在 {install_dir}\n使用 list_skills 查看已安装技能',
            )

        shutil.rmtree(install_dir)

        # 热移除
        st = ctx.cli_loop_state
        rt = st.get("runtime_ctx") if isinstance(st, dict) else None
        if rt is not None:
            from miniagent.skills.refresh import refresh_skills
            try:
                fr = await refresh_skills(
                    rt.registry, rt.skill_registry, skills_root=skills_root,
                    state=st, session_manager=st.get("session_manager"),
                )
                refresh_note = f"\n\n🔄 已从当前 Agent 中移除（移除工具 {len(fr.removed_tools)} 个）"
            except Exception as ex:
                refresh_note = f"\n\n{WARNING_PREFIX} 已删除目录但热移除失败: {ex}\n请执行 `.reload-skills` 或重启 Agent"
        else:
            refresh_note = "\n\n💡 提示：执行 `.reload-skills` 或重启 Agent 后生效"

        return ToolResult(
            success=True,
            content=f'{SUCCESS_PREFIX} 技能 "{slug}" 已卸载\n\n📁 已删除: {install_dir}{refresh_note}',
        )
    except Exception as e:
        return ToolResult(success=False, content=f'{ERROR_PREFIX} 卸载技能 "{slug}" 失败: {e}')


# ════════════════════════════════════════════════════════
# check_app_availability Handler (从 web.py 合入)
# ════════════════════════════════════════════════════════


def _check_binary(name: str) -> dict[str, Any]:
    """检查命令行工具是否可用。"""
    path = shutil.which(name)
    if path:
        return {"available": True, "path": path}
    return {"available": False, "error": f"未找到可执行文件: {name}"}


def _check_com(name: str) -> dict[str, Any]:
    """检查 Windows COM ProgID 是否可用。"""
    if os.name != "nt":
        return {"available": False, "error": "COM 检查仅支持 Windows 平台"}
    try:
        import win32com.client
        app = win32com.client.Dispatch(name)
        info: dict[str, Any] = {"available": True, "progid": name}
        for attr in ("Version", "Path", "FullName"):
            try:
                val = getattr(app, attr, None)
                if val is not None:
                    info[attr.lower()] = str(val)
            except Exception:
                pass
        try:
            getattr(app, "Quit", lambda: None)()
        except Exception:
            pass
        return info
    except Exception as e:
        return {"available": False, "error": str(e)}


def _check_env(name: str) -> dict[str, Any]:
    """检查环境变量是否已设置。"""
    value = os.environ.get(name)
    if value:
        return {"available": True, "set": True, "masked": value[:4] + "..." + value[-2:] if len(value) > 6 else "***"}
    return {"available": False, "error": f"环境变量未设置: {name}"}


def _check_python(name: str) -> dict[str, Any]:
    """检查 Python 包是否已安装。"""
    import importlib.metadata
    try:
        version = importlib.metadata.version(name)
        return {"available": True, "version": version}
    except importlib.metadata.PackageNotFoundError:
        try:
            importlib.import_module(name)
            return {"available": True, "version": "unknown"}
        except ImportError:
            return {"available": False, "error": f"Python 包未安装: {name}"}


_CHECKERS = {
    "binary": _check_binary,
    "com": _check_com,
    "env": _check_env,
    "python": _check_python,
}


async def _app_avail_handler(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    """检查指定类型的软件/依赖是否可用。"""
    check_type = str(args.get("type", ""))
    name = str(args.get("name", "")).strip()

    if not name:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} name 参数不能为空")

    checker = _CHECKERS.get(check_type)
    if not checker:
        types_str = ", ".join(_CHECKERS.keys())
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 不支持的检查类型: {check_type}（支持: {types_str}）")

    result = checker(name)

    if result.get("available"):
        lines = [f"{SUCCESS_PREFIX} {check_type}: {name} 可用"]
        for key in ("path", "progid", "version", "set", "masked"):
            if key in result:
                label = {"path": "路径", "progid": "ProgID", "version": "版本", "set": "已设置", "masked": "值"}[key]
                lines.append(f"   {label}: {result[key]}")
        return ToolResult(success=True, content="\n".join(lines))

    error = result.get("error", "未知原因不可用")
    return ToolResult(success=False, content=f"{ERROR_PREFIX} {check_type}: {name} 不可用 — {error}")


# ════════════════════════════════════════════════════════
# Tool Definitions (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

skills_tools: dict[str, ToolDefinition] = {
    "search_skills": tool("search_skills", "搜索 ClawHub 技能市场或本地已安装的技能")
        .param("query", "string", "搜索关键词")
        .enum_param("source", "搜索来源", ["clawhub", "local", "all"])
        .optional("limit", "number", "最大返回结果数（默认 10）")
        .sandbox()
        .toolbox("skills_management")
        .handler(_search_handler)
        .build(),
    "install_skill": tool("install_skill", "从 ClawHub 技能市场下载并安装一个技能")
        .param("slug", "string", "技能的 slug（唯一标识符）")
        .optional("version", "string", "版本号（可选）")
        .require_confirm()
        .toolbox("skills_management")
        .handler(_install_handler)
        .build(),
    "list_skills": tool("list_skills", "列出所有已安装的本地技能")
        .optional("verbose", "boolean", "是否显示详细信息")
        .sandbox()
        .toolbox("skills_management")
        .handler(_list_handler)
        .build(),
    "uninstall_skill": tool("uninstall_skill", "卸载一个已安装的技能（删除目录并从当前 Agent 中移除）")
        .param("slug", "string", "要卸载的技能 slug（唯一标识符）")
        .require_confirm()
        .toolbox("skills_management")
        .handler(_uninstall_handler)
        .build(),
    "check_app_availability": tool("check_app_availability", "检查指定类型的软件/依赖是否可用。支持四种检查类型：binary、com、env、python。")
        .enum_param("type", "检查类型：binary=命令行工具，com=Windows COM ProgID，env=环境变量，python=Python 包", ["binary", "com", "env", "python"])
        .param("name", "string", "检查目标的名称")
        .sandbox()
        .toolbox("skills_management")
        .handler(_app_avail_handler)
        .build(),
}

__all__ = ["skills_tools"]