"""技能包加载器 — 从技能根目录（``get_skills_root()``，默认 ``workspaces/skills``）发现包。

目录结构约定（示例）：
    workspaces/skills/
    ├── default/                 # 技能包名称（目录名 = 技能包 ID）
    │   ├── SKILL.md             # 技能包总览文档
    │   └── skills/              # 子技能目录
    │       └── file-tools/
    │           ├── SKILL.md     # 单个技能文档
    │           └── tools.py     # 工具定义入口
    └── custom/
        └── ...

加载规则：
1. 扫描技能根目录下所有一级子目录
2. 读取 SKILL.md 作为技能包文档
3. 尝试导入 __init__.py 获取 skills 列表
4. 如果不存在，尝试动态加载 skills/ 子目录

解析出的 :class:`~miniagent.types.skill.Skill` 由 ``DefaultSkillRegistry`` 索引；详见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.types.skill import Skill, SkillPackage
from miniagent.types.tool import ToolDefinition

_logger = get_logger(__name__)


# ─── SKILL.md 解析 ───────────────────────────────────────

def parse_skill_md(content: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 文件头部的 YAML front matter。

    Returns:
        (meta, body) — meta 是键值对 dict，body 是正文
    """
    meta: dict[str, str] = {}

    match = re.match(r"^---\n([\s\S]*?)\n---\n?([\s\S]*)$", content)
    if match:
        front_matter = match.group(1)
        body = match.group(2)
        for line in front_matter.split("\n"):
            kv = re.match(r"^(\w+):\s*(.+)$", line)
            if kv:
                meta[kv.group(1)] = kv.group(2).strip()
        return meta, body

    return meta, content


# ─── 动态导入模块 ────────────────────────────────────────

def _module_name_for_path(prefix: str, file_path: str) -> str:
    """按文件 mtime 生成模块名，便于 refresh 时加载更新后的 tools.py。"""
    try:
        mtime = int(os.path.getmtime(file_path))
    except OSError:
        mtime = 0
    return f"{prefix}_{mtime}"


def evict_skill_modules(*prefixes: str) -> None:
    """从 ``sys.modules`` 移除匹配前缀的技能动态模块。"""
    if not prefixes:
        return
    to_remove = [
        name
        for name in list(sys.modules)
        if any(name == p or name.startswith(f"{p}_") for p in prefixes)
    ]
    for name in to_remove:
        del sys.modules[name]


def _import_module_from_path(module_name: str, file_path: str) -> Any:
    """从文件路径动态导入 Python 模块。"""
    evict_skill_modules(module_name)
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        _logger.warning("加载 %s 失败: %s", file_path, e)
        del sys.modules[module_name]
        return None


# ─── 子技能加载 ──────────────────────────────────────────

def _load_sub_skills(skills_dir: str, *, package_name: str | None = None) -> list[Skill]:
    """从 skills/ 子目录加载子技能。"""
    pkg = package_name or os.path.basename(os.path.dirname(skills_dir))
    skills: list[Skill] = []

    for entry in sorted(os.listdir(skills_dir)):
        sub_dir = os.path.join(skills_dir, entry)
        if not os.path.isdir(sub_dir):
            continue

        skill_md_path = os.path.join(sub_dir, "SKILL.md")
        tools_py_path = os.path.join(sub_dir, "tools.py")

        # 读取 SKILL.md
        skill_md: str | None = None
        name = entry
        description = ""
        keywords: list[str] = []

        if os.path.isfile(skill_md_path):
            skill_md = Path(skill_md_path).read_text(encoding="utf-8")
            meta, body = parse_skill_md(skill_md)
            name = meta.get("name", name)
            description = meta.get("description", body[:200].strip())
            kw = meta.get("keywords", "")
            keywords = [k.strip() for k in kw.split(",") if k.strip()] if kw else []

        # 加载工具定义
        tools: dict[str, ToolDefinition] | None = None
        if os.path.isfile(tools_py_path):
            prefix = f"_skill_{pkg}_{entry}_tools"
            mod_name = _module_name_for_path(prefix, tools_py_path)
            mod = _import_module_from_path(mod_name, tools_py_path)
            if mod:
                tools = {}
                for key, value in vars(mod).items():
                    if key.startswith("_"):
                        continue
                    if isinstance(value, ToolDefinition):
                        tools[key] = value
                    elif isinstance(value, dict) and all(
                        isinstance(v, ToolDefinition) for v in value.values()
                    ):
                        tools.update(value)

        if skill_md or tools:
            skills.append(Skill(
                id=f"{os.path.basename(skills_dir)}-{entry}",
                name=name,
                description=description,
                keywords=keywords,
                tools=tools,
                skill_md=skill_md,
            ))

    return skills


# ─── 技能包加载 ──────────────────────────────────────────

async def load_skill_package(package_dir: str) -> SkillPackage | None:
    """尝试从目录加载一个技能包。

    Args:
        package_dir: 技能包目录路径

    Returns:
        SkillPackage 或 None（加载失败）
    """
    package_name = os.path.basename(package_dir)
    skill_md_path = os.path.join(package_dir, "SKILL.md")

    # 读取 SKILL.md
    skill_md: str | None = None
    name = package_name
    description = f"技能包: {package_name}"

    if os.path.isfile(skill_md_path):
        skill_md = Path(skill_md_path).read_text(encoding="utf-8")
        meta, body = parse_skill_md(skill_md)
        if meta.get("name"):
            name = meta["name"]
        if meta.get("description"):
            description = meta["description"]
        if not meta.get("name"):
            title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            if title_match:
                name = title_match.group(1).strip()

    # 尝试加载 __init__.py 中的技能定义
    skills: list[Skill] = []
    init_path = os.path.join(package_dir, "__init__.py")
    index_path = os.path.join(package_dir, "index.py")

    evict_skill_modules(f"_skillpkg_{package_name}", f"_skill_{package_name}_")

    for candidate in (init_path, index_path):
        if os.path.isfile(candidate):
            mod_name = _module_name_for_path(f"_skillpkg_{package_name}", candidate)
            mod = _import_module_from_path(mod_name, candidate)
            if mod:
                if hasattr(mod, "skills") and isinstance(mod.skills, list):
                    skills = mod.skills
                elif hasattr(mod, "default") and isinstance(mod.default, list):
                    skills = mod.default
            break

    # 尝试从 skills/ 子目录加载子技能
    sub_skills_dir = os.path.join(package_dir, "skills")
    if os.path.isdir(sub_skills_dir):
        sub_skills = _load_sub_skills(sub_skills_dir, package_name=package_name)
        skills.extend(sub_skills)

    if not skills and not skill_md:
        return None

    return SkillPackage(
        id=package_name,
        name=name,
        description=description,
        skills=skills,
        skill_md=skill_md,
        source_path=package_dir,
    )


# ─── 自动发现 ───────────────────────────────────────────

async def discover_skill_packages(skills_root: str) -> list[SkillPackage]:
    """发现并加载 skills/ 目录下的所有技能包。

    扫描指定目录下的一级子目录，每个子目录视为一个 SkillPackage。

    Args:
        skills_root: 技能目录的根路径

    Returns:
        成功加载的 SkillPackage 列表
    """
    if not os.path.isdir(skills_root):
        return []

    packages: list[SkillPackage] = []

    for entry in sorted(os.listdir(skills_root)):
        pkg_dir = os.path.join(skills_root, entry)
        if not os.path.isdir(pkg_dir):
            continue

        pkg = await load_skill_package(pkg_dir)
        if pkg:
            packages.append(pkg)

    return packages


__all__ = [
    "parse_skill_md",
    "load_skill_package",
    "discover_skill_packages",
    "evict_skill_modules",
]
