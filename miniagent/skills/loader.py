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
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from miniagent.infrastructure.logger import get_logger
from miniagent.types.skill import Skill, SkillPackage
from miniagent.types.tool import ToolDefinition

_logger = get_logger(__name__)


# ─── SKILL.md 解析 ───────────────────────────────────────


def parse_skill_md(content: str) -> tuple[dict[str, Any], str]:
    """解析 SKILL.md 文件头部的 YAML front matter。

    支持单行键值对、多行折叠块（description: |）、JSON 值（metadata: {...}）。
    YAML 解析失败时回退到简单正则逻辑。

    Returns:
        (meta, body) — meta 是键值对 dict（值保留原始类型），body 是正文
    """
    meta: dict[str, Any] = {}

    # Strip BOM if present (UTF-8 BOM = ﻿)
    if content.startswith("﻿"):
        content = content[1:]

    # Normalize CRLF to LF for consistent regex matching
    content = content.replace("\r\n", "\n")

    match = re.match(r"^---\n([\s\S]*?)\n---\n?([\s\S]*)$", content)
    if not match:
        return meta, content

    front_matter = match.group(1)
    body = match.group(2)

    # 优先使用 yaml.safe_load 解析（支持多行折叠、嵌套结构等）
    try:
        parsed = yaml.safe_load(front_matter)
        if isinstance(parsed, dict):
            return parsed, body
    except yaml.YAMLError:
        pass

    # Fallback: 简单正则（兼容旧格式）
    for line in front_matter.split("\n"):
        kv = re.match(r"^(\w+):\s*(.+)$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip()
    return meta, body


def _resolve_base_dir(content: str, base_dir: str) -> str:
    """将 OpenClaw 风格的 {baseDir} 占位符替换为实际路径。"""
    if not content or "{baseDir}" not in content:
        return content
    # 统一使用 POSIX 风格路径分隔符，避免 Windows 反斜杠问题
    posix_dir = base_dir.replace("\\", "/")
    return content.replace("{baseDir}", posix_dir)


def _map_oc_metadata(meta: dict[str, Any]) -> Any | None:
    """将 OpenClaw metadata 映射为 miniagent SkillMetadata。

    OpenClaw 格式：
        metadata: {"clawdbot":{"requires":{"bins":["node"],"env":["TAVILY_API_KEY"]}}}
    miniagent 格式：
        SkillMetadata(bins=["node"], env=["TAVILY_API_KEY"])

    也支持原生 miniagent 扁平格式：
        metadata:
          bins: [node]
          env: [TAVILY_API_KEY]

    Returns:
        SkillMetadata 实例或 None（无 metadata 时）
    """
    from miniagent.types.skill import SkillMetadata

    raw = meta.get("metadata")
    if raw is None:
        return None

    # OpenClaw JSON blob 格式（字符串形式的 JSON）
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

    bins: list[str] | None = None
    env: list[str] | None = None
    config: list[str] | None = None
    primary_env: str | None = None
    os_list: list[str] | None = None
    always: bool = False

    # 路径 1: OpenClaw 嵌套格式 metadata.clawdbot.requires.*
    if isinstance(raw, dict):
        clawdbot = raw.get("clawdbot")
        if isinstance(clawdbot, dict):
            requires = clawdbot.get("requires")
            if isinstance(requires, dict):
                bins = requires.get("bins")
                env = requires.get("env")
                config = requires.get("config")
            primary_env = clawdbot.get("primaryEnv")
            os_list = raw.get("os")
            always = raw.get("always", False)
        else:
            # 路径 2: 扁平 miniagent 格式
            bins = raw.get("bins")
            env = raw.get("env")
            config = raw.get("config")
            primary_env = raw.get("primary_env") or raw.get("primaryEnv")
            os_list = raw.get("os")
            always = raw.get("always", False)
    else:
        return None

    # 类型安全转换
    def _to_str_list(v: Any) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, list):
            return [str(x) for x in v]
        return [str(v)]

    return SkillMetadata(
        bins=_to_str_list(bins),
        env=_to_str_list(env),
        config=_to_str_list(config),
        primary_env=str(primary_env) if primary_env else None,
        os=_to_str_list(os_list),
        always=bool(always),
    )


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


def _load_sub_skills(
    skills_dir: str, *, package_name: str | None = None, inherit_metadata: Any | None = None
) -> list[Skill]:
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
        skill_metadata = None
        system_prompt = None

        if os.path.isfile(skill_md_path):
            skill_md = Path(skill_md_path).read_text(encoding="utf-8")
            meta, body = parse_skill_md(skill_md)
            # 解析 {baseDir} 占位符
            body = _resolve_base_dir(body, sub_dir)
            name = meta.get("name", name)
            description = meta.get("description", body[:200].strip())
            kw = meta.get("keywords", [])
            if isinstance(kw, str):
                keywords = [k.strip() for k in kw.split(",") if k.strip()]
            elif isinstance(kw, list):
                keywords = [str(k).strip() for k in kw if str(k).strip()]
            else:
                keywords = []
            skill_metadata = _map_oc_metadata(meta)
            system_prompt = body if body.strip() else None

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
            skills.append(
                Skill(
                    id=f"{os.path.basename(skills_dir)}-{entry}",
                    name=name,
                    description=description,
                    keywords=keywords,
                    tools=tools,
                    skill_md=skill_md,
                    system_prompt=system_prompt,
                    metadata=skill_metadata or inherit_metadata,
                )
            )

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
    skill_yaml_path = os.path.join(package_dir, "skill.yaml")
    instructions_path = os.path.join(package_dir, "instructions.md")

    # 读取 SKILL.md（优先）或 skill.yaml + instructions.md（备选）
    skill_md: str | None = None
    name = package_name
    description = f"技能包: {package_name}"

    if os.path.isfile(skill_md_path):
        skill_md = Path(skill_md_path).read_text(encoding="utf-8")
        meta, body = parse_skill_md(skill_md)
        # 解析 {baseDir} 占位符
        body = _resolve_base_dir(body, package_dir)
        if meta.get("name"):
            name = meta["name"]
        if meta.get("description"):
            description = meta["description"]
        if not meta.get("name"):
            title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            if title_match:
                name = title_match.group(1).strip()
        # 解析包级 metadata，用于继承到子技能
        package_metadata = _map_oc_metadata(meta)
    elif os.path.isfile(skill_yaml_path):
        # 备选格式：skill.yaml + instructions.md
        try:
            yaml_content = Path(skill_yaml_path).read_text(encoding="utf-8")
            meta = yaml.safe_load(yaml_content) or {}
            if isinstance(meta, dict):
                if meta.get("name"):
                    name = meta["name"]
                if meta.get("description"):
                    description = meta["description"]
                entry = meta.get("entry_point", "instructions.md")
                entry_path = os.path.join(package_dir, entry)
                if os.path.isfile(entry_path):
                    skill_md = Path(entry_path).read_text(encoding="utf-8")
                    skill_md = _resolve_base_dir(skill_md, package_dir)
                elif os.path.isfile(instructions_path):
                    skill_md = Path(instructions_path).read_text(encoding="utf-8")
                    skill_md = _resolve_base_dir(skill_md, package_dir)
        except Exception as e:
            _logger.warning("加载 skill.yaml 失败 %s: %s", package_dir, e)
        package_metadata = _map_oc_metadata(meta if isinstance(meta, dict) else {})
    else:
        package_metadata = None

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
        sub_skills = _load_sub_skills(
            sub_skills_dir, package_name=package_name, inherit_metadata=package_metadata
        )
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
    "_map_oc_metadata",
    "load_skill_package",
    "discover_skill_packages",
    "evict_skill_modules",
]
