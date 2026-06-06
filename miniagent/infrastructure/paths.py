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
    """返回 miniagent 安装/源码根目录（``config.defaults.json`` 所在目录）。"""
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


def _project_state_has_data(path: str) -> bool:
    """判断路径是否已有项目业务状态（会话或路由）。"""
    if not os.path.isdir(path):
        return False
    sessions = os.path.join(path, "sessions")
    router = os.path.join(path, "channel-router.json")
    return os.path.isdir(sessions) or os.path.isfile(router)


def resolve_project_state_dir() -> str:
    """解析项目侧 workspace 根目录（会话、路由、飞书锁等业务状态）。

    优先级（高 → 低）：

    1. ``MINIAGENT_PATHS_STATE_DIR`` 环境变量
    2. ``config`` 中的绝对路径 → ``{abs}/projects/{project_key}/``
    3. 默认 → ``{registry}/projects/{project_key}/``，含 legacy 回退：

       - 若 ``projects/{key}/`` 已存在 → 使用该目录
       - 否则若 ``{cwd}/{paths.state_dir}/`` 有历史数据 → legacy cwd 路径
       - 否则若 cwd 为 miniagent 源码根且 ``{registry}/`` 有历史数据 → registry 根
       - 否则 → 新建 ``projects/{key}/`` 路径（首次使用时创建）
    """
    env = os.environ.get(_STATE_DIR_ENV, "").strip()
    if env:
        return env

    raw = _relative_state_dir_name()
    project_key = resolve_project_key()
    registry = resolve_registry_state_dir()

    if os.path.isabs(raw):
        return os.path.join(raw, "projects", project_key)

    new_path = os.path.join(registry, "projects", project_key)
    if os.path.isdir(new_path):
        return new_path

    legacy_cwd = os.path.join(resolve_project_dir(), raw)
    if _project_state_has_data(legacy_cwd):
        return legacy_cwd

    if paths_equal(resolve_project_dir(), resolve_project_root()):
        if _project_state_has_data(registry):
            return registry

    return new_path


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


def resolve_legacy_cwd_state_dir() -> str | None:
    """旧版实例注册表根（过渡期扫描用）。

    在双路径模型之前，实例可能注册在 ``{cwd}/workspaces/instances/``。
    仅当该路径与 canonical registry 不同时返回。
    """
    legacy_instances = os.path.join(os.getcwd(), _relative_state_dir_name(), "instances")
    registry = resolve_registry_state_dir()
    legacy_root = os.path.dirname(legacy_instances)
    if paths_equal(legacy_root, registry):
        return None
    if not os.path.isdir(legacy_instances):
        return None
    return legacy_root


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
    "resolve_legacy_cwd_state_dir",
    "paths_equal",
]
