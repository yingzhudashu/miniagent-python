"""路径解析辅助（单一事实来源）。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from miniagent.infrastructure.json_config import get_config

_STATE_DIR_ENV = "MINIAGENT_PATHS_STATE_DIR"
_REGISTRY_STATE_DIR_ENV = "MINIAGENT_REGISTRY_STATE_DIR"
_PROJECT_DIR_ENV = "MINIAGENT_PROJECT_DIR"


def resolve_project_root() -> str:
    """返回 miniagent 安装/源码根目录。"""
    return str(Path(__file__).resolve().parent.parent.parent)


def normalize_project_dir(path: str) -> str:
    """规范化项目目录路径（realpath + normcase）。"""
    return os.path.normcase(os.path.normpath(os.path.realpath(path)))


def resolve_project_dir() -> str:
    """当前 Agent 绑定的项目目录（启动时 cwd，可通过 ``MINIAGENT_PROJECT_DIR`` 覆盖）。"""
    env = os.environ.get(_PROJECT_DIR_ENV, "").strip()
    if env:
        return normalize_project_dir(env)
    return normalize_project_dir(os.getcwd())


def resolve_project_key(project_dir: str | None = None) -> str:
    """由项目目录生成稳定的 workspace 命名空间键（``{basename}-{hash8}``）。"""
    path = normalize_project_dir(project_dir or resolve_project_dir())
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]
    base = os.path.basename(path.rstrip(os.sep)) or "root"
    safe = re.sub(r"[^\w\-]+", "-", base).strip("-")[:32] or "proj"
    return f"{safe}-{digest}"


def _relative_state_dir_name() -> str:
    raw = get_config("paths.state_dir", "workspaces")
    if not raw or not str(raw).strip():
        return "workspaces"
    return str(raw).strip()


def resolve_project_state_dir() -> str:
    """解析项目侧 workspace 根目录（会话、路由、飞书锁等业务状态）。

    优先级（高 → 低）：

    1. ``MINIAGENT_PATHS_STATE_DIR`` 环境变量
    2. ``config`` 中的绝对路径 → ``{abs}/projects/{project_key}/``
    3. 默认 → ``{registry}/projects/{project_key}/``

    解析结果只由配置与项目目录决定，不探测磁盘中的其它状态目录。
    """
    env = os.environ.get(_STATE_DIR_ENV, "").strip()
    if env:
        return env

    raw = _relative_state_dir_name()
    project_key = resolve_project_key()
    registry = resolve_registry_state_dir()

    if os.path.isabs(raw):
        return os.path.join(raw, "projects", project_key)

    return os.path.join(registry, "projects", project_key)


def resolve_registry_state_dir() -> str:
    """解析全局实例注册表根目录（``instances/`` 所在的状态根）。

    始终指向 miniagent 仓库/安装根下的 ``workspaces``，不受
    ``MINIAGENT_PATHS_STATE_DIR`` 影响。测试可通过 ``MINIAGENT_REGISTRY_STATE_DIR`` 覆盖。
    """
    env = os.environ.get(_REGISTRY_STATE_DIR_ENV, "").strip()
    if env:
        return env
    return os.path.join(resolve_project_root(), "workspaces")


def resolve_state_dir() -> str:
    """解析运行时项目状态根目录（与 ``resolve_project_state_dir()`` 同义）。"""
    return resolve_project_state_dir()


def paths_equal(a: str, b: str) -> bool:
    """跨平台路径等价比较（normpath + normcase）。"""
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


__all__ = [
    "normalize_project_dir",
    "resolve_project_root",
    "resolve_project_dir",
    "resolve_project_key",
    "resolve_project_state_dir",
    "resolve_registry_state_dir",
    "resolve_state_dir",
    "paths_equal",
]
