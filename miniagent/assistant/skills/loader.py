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

解析出的 :class:`~miniagent.agent.types.skill.Skill` 由 ``DefaultSkillRegistry`` 索引；详见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from miniagent.agent.logging import get_logger
from miniagent.agent.types.skill import Skill, SkillPackage
from miniagent.agent.types.tool import ToolDefinition

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
    except yaml.YAMLError as e:
        _logger.debug("YAML解析失败，使用正则回退: %s", e)

    # Fallback: 简单正则（YAML 解析失败时的兜底）
    for line in front_matter.split("\n"):
        kv = re.match(r"^(\w+):\s*(.+)$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip()
    return meta, body


def _resolve_base_dir(content: str, base_dir: str) -> str:
    """将 {baseDir} 占位符替换为技能目录路径。"""
    if not content or "{baseDir}" not in content:
        return content
    # 统一使用 POSIX 风格路径分隔符，避免 Windows 反斜杠问题
    posix_dir = base_dir.replace("\\", "/")
    return content.replace("{baseDir}", posix_dir)


def _keywords_from_meta(meta: dict[str, Any]) -> list[str]:
    """从 front matter 解析 keywords 列表。"""
    kw = meta.get("keywords", [])
    if isinstance(kw, str):
        return [k.strip() for k in kw.split(",") if k.strip()]
    if isinstance(kw, list):
        return [str(k).strip() for k in kw if str(k).strip()]
    return []


def _description_from_meta(meta: dict[str, Any], body: str) -> str:
    """从 front matter 或正文提取描述文本。"""
    raw = meta.get("description")
    if raw is not None:
        if isinstance(raw, str):
            return raw.strip()
        return str(raw).strip()
    return body[:200].strip() if body.strip() else ""


def _map_metadata(meta: dict[str, Any]) -> Any | None:
    """将 metadata 映射为 SkillMetadata。

    支持扁平格式：
        metadata:
          bins: [node]
          env: [TAVILY_API_KEY]
          always: true
          os: [linux, darwin]

    Returns:
        SkillMetadata 实例或 None（无 metadata 时）
    """
    from miniagent.agent.types.skill import SkillMetadata

    raw = meta.get("metadata")
    if raw is None:
        return None

    # JSON 字符串格式（字符串形式的 JSON）
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None

    if not isinstance(raw, dict):
        return None

    # 类型安全转换
    def _to_str_list(v: Any) -> list[str] | None:
        """将值转换为字符串列表（支持 None、列表、单值）。"""
        if v is None:
            return None
        if isinstance(v, list):
            return [str(x) for x in v]
        return [str(v)]

    def _to_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    return SkillMetadata(
        bins=_to_str_list(raw.get("bins")),
        com=_to_str_list(raw.get("com")),
        env=_to_str_list(raw.get("env")),
        config=_to_str_list(raw.get("config")),
        primary_env=str(raw.get("primary_env") or raw.get("primaryEnv") or "") or None,
        os=_to_str_list(raw.get("os")),
        always=_to_bool(raw.get("always"), False),
        skill_key=str(raw.get("skill_key") or raw.get("skillKey") or "") or None,
        user_invocable=_to_bool(raw.get("user_invocable", raw.get("userInvocable")), True),
        disable_model_invocation=_to_bool(
            raw.get("disable_model_invocation", raw.get("disableModelInvocation")),
            False,
        ),
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
            keywords = _keywords_from_meta(meta)
            skill_metadata = _map_metadata(meta)
            system_prompt = body if body.strip() else None

        # 加载工具定义
        tools: dict[str, ToolDefinition] = {}
        if os.path.isfile(tools_py_path):
            prefix = f"_skill_{pkg}_{entry}_tools"
            mod_name = _module_name_for_path(prefix, tools_py_path)
            mod = _import_module_from_path(mod_name, tools_py_path)
            if mod:
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
                    id=f"{pkg}-{entry}",
                    name=name,
                    description=description,
                    keywords=keywords,
                    tools=tools,
                    skill_md=skill_md,
                    system_prompt=system_prompt,
                    metadata=skill_metadata or inherit_metadata,
                    source_path=sub_dir,
                )
            )

    return skills


# ─── 技能包加载 ──────────────────────────────────────────


@dataclass(frozen=True)
class _PackageManifest:
    """保存技能包文档解析结果，避免加载流程共享可变局部状态。"""

    name: str
    description: str
    metadata: dict[str, Any]
    body: str
    skill_md: str | None
    skill_metadata: Any | None


def _read_skill_yaml(package_dir: str, yaml_path: str) -> tuple[dict[str, Any], str | None, str]:
    """读取旧式 YAML 技能清单及其指令入口。"""
    meta: dict[str, Any] = {}
    skill_md: str | None = None
    body = ""
    try:
        loaded = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            return meta, skill_md, body
        meta = loaded
        entry_path = os.path.join(package_dir, str(meta.get("entry_point", "instructions.md")))
        fallback_path = os.path.join(package_dir, "instructions.md")
        selected_path = entry_path if os.path.isfile(entry_path) else fallback_path
        if os.path.isfile(selected_path):
            skill_md = _resolve_base_dir(Path(selected_path).read_text(encoding="utf-8"), package_dir)
            instruction_meta, body = parse_skill_md(skill_md)
            body = _resolve_base_dir(body, package_dir)
            if instruction_meta:
                meta = {**meta, **instruction_meta}
    except Exception as error:
        _logger.warning("加载 skill.yaml 失败 %s: %s", package_dir, error)
    return meta, skill_md, body


def _load_package_manifest(package_dir: str, package_name: str) -> _PackageManifest:
    """按 ``SKILL.md`` 优先、YAML 兼容的顺序读取包级清单。"""
    skill_md_path = os.path.join(package_dir, "SKILL.md")
    skill_yaml_path = os.path.join(package_dir, "skill.yaml")
    default_description = f"技能包: {package_name}"
    if os.path.isfile(skill_md_path):
        skill_md = Path(skill_md_path).read_text(encoding="utf-8")
        metadata, body = parse_skill_md(skill_md)
        body = _resolve_base_dir(body, package_dir)
        name = str(metadata.get("name") or package_name)
        if not metadata.get("name"):
            title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            if title_match:
                name = title_match.group(1).strip()
        return _PackageManifest(
            name=name,
            description=_description_from_meta(metadata, body) or default_description,
            metadata=metadata,
            body=body,
            skill_md=skill_md,
            skill_metadata=_map_metadata(metadata),
        )
    if os.path.isfile(skill_yaml_path):
        metadata, yaml_skill_md, body = _read_skill_yaml(package_dir, skill_yaml_path)
        return _PackageManifest(
            name=str(metadata.get("name") or package_name),
            description=_description_from_meta(metadata, body) or default_description,
            metadata=metadata,
            body=body,
            skill_md=yaml_skill_md,
            skill_metadata=_map_metadata(metadata),
        )
    return _PackageManifest(package_name, default_description, {}, "", None, None)


def _load_python_skill_definitions(package_dir: str, package_name: str) -> list[Skill]:
    """加载包入口公开的技能定义，并保证刷新时不复用旧模块。"""
    evict_skill_modules(f"_skillpkg_{package_name}", f"_skill_{package_name}_")
    for filename in ("__init__.py", "index.py"):
        candidate = os.path.join(package_dir, filename)
        if not os.path.isfile(candidate):
            continue
        module_name = _module_name_for_path(f"_skillpkg_{package_name}", candidate)
        module = _import_module_from_path(module_name, candidate)
        if module and isinstance(getattr(module, "skills", None), list):
            return module.skills
        if module and isinstance(getattr(module, "default", None), list):
            return module.default
        break
    return []


def _build_instruction_only_skill(
    package_dir: str, package_name: str, manifest: _PackageManifest
) -> Skill:
    """把没有 Python 或子技能定义的包级指令合成为单个技能。"""
    body = manifest.body
    if not body.strip() and manifest.skill_md:
        _, body = parse_skill_md(manifest.skill_md)
        body = _resolve_base_dir(body, package_dir)
    return Skill(
        id=package_name,
        name=manifest.name,
        description=manifest.description,
        keywords=_keywords_from_meta(manifest.metadata),
        skill_md=manifest.skill_md,
        system_prompt=body if body.strip() else None,
        metadata=manifest.skill_metadata,
        source_path=package_dir,
    )


def _load_skill_package_sync(package_dir: str) -> SkillPackage | None:
    """尝试从目录加载一个技能包。

    Args:
        package_dir: 技能包目录路径

    Returns:
        SkillPackage 或 None（加载失败）
    """
    package_name = os.path.basename(package_dir)
    manifest = _load_package_manifest(package_dir, package_name)
    skills = _load_python_skill_definitions(package_dir, package_name)

    # 尝试从 skills/ 子目录加载子技能
    sub_skills_dir = os.path.join(package_dir, "skills")
    if os.path.isdir(sub_skills_dir):
        sub_skills = _load_sub_skills(
            sub_skills_dir,
            package_name=package_name,
            inherit_metadata=manifest.skill_metadata,
        )
        skills.extend(sub_skills)

    # 纯指令型包：无子技能时，用包级 SKILL.md 合成一个 Skill 以注入 system prompt
    if not skills and manifest.skill_md:
        skills.append(_build_instruction_only_skill(package_dir, package_name, manifest))

    if not skills and not manifest.skill_md:
        return None

    return SkillPackage(
        id=package_name,
        name=manifest.name,
        description=manifest.description,
        skills=skills,
        skill_md=manifest.skill_md,
        source_path=package_dir,
    )


async def load_skill_package(package_dir: str) -> SkillPackage | None:
    """Load and import one skill package without blocking the event loop."""
    return await asyncio.to_thread(_load_skill_package_sync, package_dir)


# ─── 自动发现 ───────────────────────────────────────────


async def discover_skill_packages(skills_root: str) -> list[SkillPackage]:
    """发现并加载 skills/ 目录下的所有技能包。

    扫描指定目录下的一级子目录，每个子目录视为一个 SkillPackage。

    Args:
        skills_root: 技能目录的根路径

    Returns:
        成功加载的 SkillPackage 列表
    """
    def _discover_sync() -> list[SkillPackage]:
        if not os.path.isdir(skills_root):
            return []
        packages: list[SkillPackage] = []
        for entry in sorted(os.listdir(skills_root)):
            pkg_dir = os.path.join(skills_root, entry)
            if not os.path.isdir(pkg_dir):
                continue
            pkg = _load_skill_package_sync(pkg_dir)
            if pkg:
                packages.append(pkg)
        return packages

    return await asyncio.to_thread(_discover_sync)


__all__ = [
    "parse_skill_md",
    "load_skill_package",
    "discover_skill_packages",
    "evict_skill_modules",
]
