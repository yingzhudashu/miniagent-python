"""Mini Agent Python — 多实例注册表

支持多个实例同时运行，每个实例独立注册、心跳、注销。

**存活与清理（重要）**

- 是否在列表中显示、是否删除磁盘目录，均以 **操作系统 PID 是否存在** 为准（``is_process_running``）。
- ``register()`` 在分配新 ``instance_id`` **之前** 会扫描并删除 PID 已失效的目录；**不会**向其它进程发送终止信号。
- ``heartbeat`` 文件仍会更新，便于人工排查；**不参与**「是否存活」的权威判定，避免心跳写入滞后导致误删仍在运行的实例注册信息。

运维向说明见 ``docs/ENGINEERING.md`` §3.3。

实例注册表结构：
    workspaces/
    └── instances/
        ├── 1/
        │   ├── meta.json      ← 实例元数据
        │   └── heartbeat      ← 心跳时间戳
        ├── 2/
        │   ├── meta.json
        │   └── heartbeat
        └── ...

实例元数据（meta.json）：
    {
        "pid": 12345,
        "instance_id": 1,
        "start_time": "2026-05-09T10:00:00",
        "mode": "cli",          ← "cli" 仅 CLI | "both" CLI+飞书（无独立「纯飞书」进程模式）
        "active_sessions": ["default", "cli-interactive"],
        "hostname": "ZXB-PC"
    }

使用方式：
    mgr = InstanceRegistry()
    mgr.register(mode="cli")
    # ... 运行期间定期 mgr.heartbeat() ...
    instances = mgr.list_all()
    mgr.unregister()  # 退出时调用

**重构说明**：
- PID 检测函数已提取到公共模块 ``miniagent/infrastructure/process_utils.py``
- 本模块导入并再导出以保持 API 兼容性
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from miniagent.core.constants import INSTANCE_CACHE_TTL, INSTANCE_HEARTBEAT_TIMEOUT
from miniagent.infrastructure.logger import get_logger
from miniagent.types.error_prefix import ERROR_PREFIX
from miniagent.infrastructure.process_utils import (
    is_process_running,
    is_process_running_async,
)

HEARTBEAT_TIMEOUT = INSTANCE_HEARTBEAT_TIMEOUT

_logger = get_logger(__name__)

# 实例 mode 仅两种：始终有 CLI 主循环；both 表示同进程已启用飞书连接
_VALID_INSTANCE_MODES = frozenset({"cli", "both"})
_REGISTER_ID_MAX_ATTEMPTS = 50


class ProjectDirConflictError(Exception):
    """同一项目目录已有存活实例时抛出。"""

    def __init__(self, existing_meta: dict[str, Any]) -> None:
        """Args:
            existing_meta: 冲突实例的 ``meta.json`` 内容（用于格式化提示）。
        """
        self.existing_meta = existing_meta
        super().__init__(format_project_conflict_message(existing_meta))


def format_project_conflict_message(meta: dict[str, Any]) -> str:
    """格式化同项目目录已有存活实例时的错误提示。"""
    iid = meta.get("instance_id", "?")
    pid = meta.get("pid", "?")
    project_dir = meta.get("project_dir", "?")
    state_dir = meta.get("project_state_dir", "")
    if not state_dir and project_dir and project_dir != "?":
        from miniagent.infrastructure.paths import (
            resolve_project_key,
            resolve_registry_state_dir,
        )

        key = meta.get("project_key") or resolve_project_key(str(project_dir))
        state_dir = os.path.join(resolve_registry_state_dir(), "projects", key)
    msg = (
        f"{ERROR_PREFIX} 项目目录 {project_dir!r} 已有运行中的实例 #{iid} (PID={pid})。"
        "请先执行 `python -m miniagent --stop` 停止后再启动。"
    )
    if state_dir:
        msg += f"\n   数据目录: {state_dir}"
    return msg


def _validate_instance_mode(mode: str) -> None:
    """校验实例 mode 属于 ``cli`` / ``both``，否则抛 ``ValueError``。"""
    if mode not in _VALID_INSTANCE_MODES:
        raise ValueError(f"instance mode must be 'cli' or 'both', got {mode!r}")


def _ensure_instances_dir(state_dir: str) -> Path:
    """确保实例注册目录存在。"""
    inst_dir = Path(state_dir) / "instances"
    inst_dir.mkdir(parents=True, exist_ok=True)
    return inst_dir


def _get_registry_state_dir(state_dir: str | None = None) -> str:
    """获取实例注册表状态根目录。"""
    from miniagent.infrastructure.paths import resolve_registry_state_dir

    return state_dir or resolve_registry_state_dir()


def _meta_project_dir(meta: dict[str, Any]) -> str:
    """从 meta 解析项目目录；旧 meta 无字段时回退为 miniagent 源码根。"""
    from miniagent.infrastructure.paths import (
        normalize_project_dir,
        resolve_project_root,
    )

    raw = meta.get("project_dir")
    if raw:
        return normalize_project_dir(str(raw))
    return normalize_project_dir(resolve_project_root())


def find_alive_instance_for_project(
    project_dir: str,
    *,
    state_dir: str | None = None,
    pid_checker: Any = None,
) -> dict[str, Any] | None:
    """查找占用指定项目目录的存活实例（无则返回 ``None``）。"""
    from miniagent.infrastructure.paths import normalize_project_dir, paths_equal

    target = normalize_project_dir(project_dir)
    checker = pid_checker or is_process_running
    for root in _instance_registry_roots():
        reg = InstanceRegistry(state_dir=root, pid_checker=checker)
        for inst in reg.list_all():
            if paths_equal(_meta_project_dir(inst), target):
                return inst
    return None


# PID 检测函数已移至 miniagent.infrastructure.process_utils
# 以下为再导出，保持 API 兼容性
# is_process_running 和 is_process_running_async 从 process_utils 导入


def _next_instance_id(inst_dir: Path) -> int:
    """获取下一个实例 ID（自增）。"""
    max_id = 0
    for entry in inst_dir.iterdir():
        if entry.is_dir() and entry.name.isdigit():
            max_id = max(max_id, int(entry.name))
    return max_id + 1


@contextlib.contextmanager
def _registry_file_lock(inst_dir: Path) -> Iterator[None]:
    """跨进程互斥锁：保护实例 ID 分配与 meta 写入。"""
    lock_path = inst_dir / ".registry.lock"
    inst_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_f:
        try:
            if sys.platform == "win32":
                lock_f.seek(0)
                msvcrt.locking(lock_f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if sys.platform == "win32":
                try:
                    lock_f.seek(0)
                    msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError as e:
                    _logger.debug("Windows 注册表锁解锁失败: %s", e)
            else:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                except OSError as e:
                    _logger.debug("Unix 注册表锁解锁失败: %s", e)


def _short_state_dir_label(state_dir: str, *, canonical: str | None = None) -> str:
    """表格用短路径标签。"""
    norm = os.path.normpath(state_dir)
    if canonical and os.path.normcase(norm) == os.path.normcase(os.path.normpath(canonical)):
        return "canonical"
    base = os.path.basename(norm) or norm
    parent = os.path.basename(os.path.dirname(norm))
    if parent and parent not in (".", ".."):
        return f"{parent}/{base}"
    return base


def _instance_registry_roots(*, include_legacy_cwd: bool = True) -> list[str]:
    """返回需要扫描的实例注册表状态根（registry + 可选 legacy cwd）。"""
    from miniagent.infrastructure.paths import (
        resolve_legacy_cwd_state_dir,
        resolve_registry_state_dir,
    )

    roots: list[str] = []
    registry = resolve_registry_state_dir()
    for candidate in (registry, resolve_legacy_cwd_state_dir() if include_legacy_cwd else None):
        if not candidate:
            continue
        norm = os.path.normcase(os.path.normpath(candidate))
        if any(os.path.normcase(os.path.normpath(r)) == norm for r in roots):
            continue
        roots.append(candidate)
    return roots


# ============================================================================
# InstanceRegistry
# ============================================================================


class InstanceRegistry:
    """多实例注册表

    每个 Agent 实例启动时注册，运行期间定期心跳，退出时注销。
    其他实例/CLI 可通过 list_all() 查看所有存活实例。

    Example:
        mgr = InstanceRegistry()
        mgr.register(mode="cli")
        try:
            # ... 主循环 ...
            mgr.heartbeat()  # 定期调用
        finally:
            mgr.unregister()
    """

    def __init__(
        self,
        state_dir: str | None = None,
        pid_checker: Any = None,
    ) -> None:
        """Args:
        state_dir: 注册表状态根；默认 ``resolve_registry_state_dir()``（仓库 ``workspaces``）。
        pid_checker: 可注入的 PID 存活探测（测试用）。
        """
        self._state_dir = _get_registry_state_dir(state_dir)
        self._inst_dir = _ensure_instances_dir(self._state_dir)
        self._my_id: int | None = None
        self._my_dir: Path | None = None
        self._meta: dict[str, Any] = {}
        self._pid_checker = pid_checker or is_process_running

    # ─── 生命周期 ───

    def register(
        self,
        mode: str = "cli",
        active_sessions: list[str] | None = None,
    ) -> dict[str, Any]:
        """注册当前实例到注册表。

        Args:
            mode: "cli"（仅 CLI，飞书未启用）或 "both"（CLI + 飞书已启用）
            active_sessions: 初始会话列表

        Returns:
            实例元数据
        """
        from miniagent.infrastructure.paths import (
            resolve_project_dir,
            resolve_project_key,
            resolve_project_state_dir,
        )

        project_dir = resolve_project_dir()
        project_state_dir = resolve_project_state_dir()
        project_key = resolve_project_key(project_dir)

        _validate_instance_mode(mode)
        with _registry_file_lock(self._inst_dir):
            self._cleanup_dead_registered_instances()
            self._assert_no_project_dir_conflict(project_dir)
            my_dir: Path | None = None
            inst_id = 0
            for _ in range(_REGISTER_ID_MAX_ATTEMPTS):
                inst_id = _next_instance_id(self._inst_dir)
                candidate = self._inst_dir / str(inst_id)
                meta_file = candidate / "meta.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, encoding="utf-8") as f:
                            existing = json.load(f)
                        if self._is_pid_alive(existing):
                            continue
                    except Exception:
                        pass
                    try:
                        shutil.rmtree(candidate)
                    except Exception as e:
                        _logger.debug("清理冲突实例目录失败: %s", e)
                        continue
                candidate.mkdir(parents=True, exist_ok=True)
                my_dir = candidate
                break
            else:
                raise RuntimeError("无法分配实例 ID：注册表目录已满或持续冲突")

            self._meta = {
                "pid": os.getpid(),
                "instance_id": inst_id,
                "start_time": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "active_sessions": active_sessions or [],
                "hostname": socket.gethostname(),
                "project_dir": project_dir,
                "project_key": project_key,
                "project_state_dir": project_state_dir,
            }

            self._my_id = inst_id
            self._my_dir = my_dir

            self._write_meta()
            self.heartbeat()

        _logger.info(
            "实例已注册: #%d (PID=%d, mode=%s)",
            inst_id,
            os.getpid(),
            mode,
        )
        return dict(self._meta)

    def heartbeat(self) -> None:
        """更新当前实例心跳。"""
        if self._my_dir:
            heartbeat_file = self._my_dir / "heartbeat"
            heartbeat_file.write_text(f"{time.time():.0f}\n", encoding="utf-8")

    def unregister(self) -> None:
        """注销当前实例（退出时调用）。"""
        if self._my_dir and self._my_dir.exists():
            try:
                shutil.rmtree(self._my_dir)
                _logger.info("实例已注销: #%d", self._my_id)
            except Exception as e:
                _logger.warning("实例注销失败: %s", e)
        self._my_id = None
        self._my_dir = None

    # ─── 查询 ───

    def list_all(self, *, attach_state_dir: bool = False) -> list[dict[str, Any]]:
        """列出所有存活实例。

        Returns:
            存活实例元数据列表，按 instance_id 排序。
        """
        results = []
        if not self._inst_dir.exists():
            return results

        for entry in sorted(
            self._inst_dir.iterdir(), key=lambda e: int(e.name) if e.name.isdigit() else 0
        ):
            if not entry.is_dir() or not entry.name.isdigit():
                continue

            meta_file = entry / "meta.json"

            if not meta_file.exists():
                continue

            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta = json.load(f)

                is_alive = self._is_pid_alive(meta)

                meta["alive"] = is_alive
                meta["instance_dir"] = str(entry)
                if attach_state_dir:
                    meta["state_dir"] = self._state_dir

                if is_alive:
                    results.append(meta)
                else:
                    try:
                        shutil.rmtree(entry)
                    except Exception as e:
                        _logger.warning("清理失效实例目录失败: %s - %s", entry, e)

            except Exception as e:
                _logger.warning("读取实例元数据失败: %s - %s", entry, e)

        return results

    def get(self, instance_id: int) -> dict[str, Any] | None:
        """获取指定实例信息。"""
        for inst in self.list_all():
            if inst["instance_id"] == instance_id:
                return inst
        return None

    def stop(self, instance_id: int) -> dict[str, Any]:
        """停止指定实例。

        Args:
            instance_id: 目标实例 ID

        Returns:
            {"success": True} 或 {"success": False, "reason": str}
        """
        inst_dir = self._inst_dir / str(instance_id)
        meta_file = inst_dir / "meta.json"

        if not meta_file.exists():
            return {"success": False, "reason": f"实例 #{instance_id} 不存在"}

        try:
            with open(meta_file, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as e:
            return {"success": False, "reason": f"读取元数据失败: {e}"}

        pid = meta.get("pid", 0)
        if not is_process_running(pid):
            # 进程已死亡，清理残留
            try:
                shutil.rmtree(inst_dir)
            except Exception as e:
                _logger.debug("清理失效实例目录失败: %s", e)
            return {
                "success": True,
                "reason": f"实例 #{instance_id} (PID={pid}) 已不存在，已清理",
            }

        # 终止进程
        _logger.info("正在停止实例 #%d (PID=%d)...", instance_id, pid)
        try:
            if sys.platform == "win32":
                subprocess.check_output(
                    ["taskkill", "/PID", str(pid), "/F"],
                    timeout=10,
                )
            else:
                os.kill(pid, 15)
                for _ in range(50):
                    if not is_process_running(pid):
                        break
                    time.sleep(0.1)
        except Exception as e:
            return {"success": False, "reason": f"无法终止 PID={pid}: {e}"}

        # 清理注册表目录
        try:
            shutil.rmtree(inst_dir)
        except Exception as e:
            _logger.debug("清理实例注册目录失败: %s", e)

        _logger.info("实例 #%d 已停止", instance_id)
        return {"success": True}

    async def stop_async(self, instance_id: int) -> dict[str, Any]:
        """异步停止指定实例（不阻塞事件循环）。

        用于异步上下文（如 ticker、CLI 命令）中停止其他实例，
        避免 subprocess.check_output 和 time.sleep 阻塞。

        Args:
            instance_id: 目标实例 ID

        Returns:
            {"success": True} 或 {"success": False, "reason": str}
        """
        inst_dir = self._inst_dir / str(instance_id)
        meta_file = inst_dir / "meta.json"

        if not meta_file.exists():
            return {"success": False, "reason": f"实例 #{instance_id} 不存在"}

        try:
            with open(meta_file, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as e:
            return {"success": False, "reason": f"读取元数据失败: {e}"}

        pid = meta.get("pid", 0)
        if not await is_process_running_async(pid):
            # 进程已死亡，清理残留
            try:
                shutil.rmtree(inst_dir)
            except Exception as e:
                _logger.debug("清理失效实例目录失败: %s", e)
            return {
                "success": True,
                "reason": f"实例 #{instance_id} (PID={pid}) 已不存在，已清理",
            }

        # 终止进程
        _logger.info("正在停止实例 #%d (PID=%d)...", instance_id, pid)
        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(pid),
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            else:
                os.kill(pid, 15)
                for _ in range(50):
                    if not await is_process_running_async(pid):
                        break
                    await asyncio.sleep(0.1)
        except Exception as e:
            return {"success": False, "reason": f"无法终止 PID={pid}: {e}"}

        # 清理注册表目录
        try:
            shutil.rmtree(inst_dir)
        except Exception as e:
            _logger.debug("清理实例注册目录失败: %s", e)

        _logger.info("实例 #%d 已停止", instance_id)
        return {"success": True}

    def stop_current(self) -> dict[str, Any]:
        """停止当前实例（当前进程）。"""
        if self._my_id is None:
            return {"success": False, "reason": "当前未注册实例"}
        # 当前进程不能 kill 自己，标记退出即可
        self.unregister()
        return {"success": True}

    # ─── 更新 ───

    def update_sessions(self, active_sessions: list[str]) -> None:
        """更新当前实例的活跃会话列表。"""
        if self._my_id is None:
            return
        self._meta["active_sessions"] = active_sessions
        self._write_meta()

    def update_mode(self, mode: str) -> None:
        """更新当前实例的 mode（与飞书运行时开关同步）。"""
        if self._my_id is None:
            return
        _validate_instance_mode(mode)
        self._meta["mode"] = mode
        self._write_meta()

    # ─── 内部 ───

    def _write_meta(self) -> None:
        """写入元数据文件。"""
        if self._my_dir:
            meta_file = self._my_dir / "meta.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(self._meta, f, indent=2, ensure_ascii=False)

    def _is_pid_alive(self, meta: dict[str, Any]) -> bool:
        """以操作系统进程是否存在判定实例是否仍在运行。"""
        try:
            pid = int(meta.get("pid") or 0)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        return bool(self._pid_checker(pid))

    def _cleanup_dead_registered_instances(self) -> None:
        """删除注册表中 PID 已不存在的实例目录（不终止进程）。"""
        if not self._inst_dir.exists():
            return

        for entry in sorted(
            self._inst_dir.iterdir(),
            key=lambda e: int(e.name) if e.name.isdigit() else 0,
        ):
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            meta_file = entry / "meta.json"
            if not meta_file.exists():
                continue
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta = json.load(f)
                if self._is_pid_alive(meta):
                    continue
                shutil.rmtree(entry)
            except Exception as e:
                _logger.debug("清理失效实例目录失败: %s", e)

    def _assert_no_project_dir_conflict(self, project_dir: str) -> None:
        """同一项目目录仅允许一个存活实例。"""
        from miniagent.infrastructure.paths import normalize_project_dir, paths_equal

        target = normalize_project_dir(project_dir)
        if not self._inst_dir.exists():
            return
        for entry in self._inst_dir.iterdir():
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            meta_file = entry / "meta.json"
            if not meta_file.exists():
                continue
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            if not self._is_pid_alive(meta):
                continue
            if paths_equal(_meta_project_dir(meta), target):
                raise ProjectDirConflictError(meta)


# ─── 模块级便捷函数 ───

_default_registry: InstanceRegistry | None = None

# ── 性能优化：实例列表缓存（按 cache_key 分键）──
_instance_list_caches: dict[tuple[str | None, bool], tuple[float, list[dict[str, Any]]]] = {}
# 使用 constants.py 中定义的 TTL

# 并发安全：全局单例创建锁
_instance_registry_lock = threading.Lock()


def _clear_instance_list_caches() -> None:
    """清空实例列表缓存。"""
    global _instance_list_caches
    _instance_list_caches = {}


def get_registry(state_dir: str | None = None) -> InstanceRegistry:
    """获取或创建默认实例注册表（线程安全）。

    使用锁保护单例创建，避免多线程首次调用时创建多个实例。
    """
    global _default_registry
    with _instance_registry_lock:
        if _default_registry is None:
            _default_registry = InstanceRegistry(state_dir)
        return _default_registry


def register_instance(
    mode: str = "cli",
    active_sessions: list[str] | None = None,
    state_dir: str | None = None,
) -> dict[str, Any]:
    """注册当前实例。mode 仅 ``cli`` 或 ``both``（CLI + 飞书）。"""
    _clear_instance_list_caches()
    return get_registry(state_dir).register(mode, active_sessions)


def update_instance_mode(mode: str, state_dir: str | None = None) -> None:
    """更新当前进程已注册实例的 mode（供飞书 start/stop 同步 meta）。"""
    get_registry(state_dir).update_mode(mode)


def heartbeat(state_dir: str | None = None) -> None:
    """更新当前实例心跳。"""
    get_registry(state_dir).heartbeat()


def unregister_instance(state_dir: str | None = None) -> None:
    """注销当前实例。"""
    _clear_instance_list_caches()
    get_registry(state_dir).unregister()


def list_instances(
    state_dir: str | None = None,
    *,
    include_legacy_cwd: bool = True,
) -> list[dict[str, Any]]:
    """列出所有存活实例（可聚合 canonical + legacy cwd 注册表）。"""
    if state_dir is not None:
        reg = InstanceRegistry(state_dir=state_dir)
        return reg.list_all(attach_state_dir=True)

    results: list[dict[str, Any]] = []
    for root in _instance_registry_roots(include_legacy_cwd=include_legacy_cwd):
        reg = InstanceRegistry(state_dir=root)
        results.extend(reg.list_all(attach_state_dir=True))
    results.sort(
        key=lambda i: (
            str(i.get("state_dir", "")),
            int(i.get("instance_id") or 0),
        )
    )
    return results


def list_instances_cached(
    state_dir: str | None = None,
    *,
    include_legacy_cwd: bool = True,
) -> list[dict[str, Any]]:
    """列出所有存活实例（带缓存，性能优化）。

    缓存 5 秒有效，按 ``(state_dir, include_legacy_cwd)`` 分键。
    注册/注销操作会自动清除缓存。

    Args:
        state_dir: 状态目录
        include_legacy_cwd: 是否聚合 legacy cwd 注册表

    Returns:
        存活实例列表
    """
    global _instance_list_caches
    cache_key = (state_dir, include_legacy_cwd)
    now = time.time()
    cached = _instance_list_caches.get(cache_key)
    if cached is not None and now - cached[0] < INSTANCE_CACHE_TTL:
        return cached[1]
    result = list_instances(state_dir, include_legacy_cwd=include_legacy_cwd)
    _instance_list_caches[cache_key] = (now, result)
    return result


def stop_instance_by_id(
    instance_id: int,
    state_dir: str | None = None,
) -> dict[str, Any]:
    """停止指定实例。``state_dir`` 省略时在已聚合列表中查找；多根同 ID 时需显式指定。"""
    _clear_instance_list_caches()

    target_dir = state_dir
    if target_dir is None:
        matches = [
            i
            for i in list_instances()
            if int(i.get("instance_id") or 0) == instance_id
        ]
        if not matches:
            return {"success": False, "reason": f"实例 #{instance_id} 不存在"}
        if len(matches) > 1:
            dirs = ", ".join(sorted({str(m.get("state_dir", "?")) for m in matches}))
            return {
                "success": False,
                "reason": (
                    f"实例 #{instance_id} 存在于多个状态目录（{dirs}），"
                    "请使用 stop_instance_by_id(id, state_dir=...) 或 "
                    "python -m miniagent --stop --state-dir <路径> <id>"
                ),
            }
        target_dir = str(matches[0].get("state_dir") or "")
        if not target_dir:
            from miniagent.infrastructure.paths import resolve_registry_state_dir

            target_dir = resolve_registry_state_dir()

    return InstanceRegistry(state_dir=target_dir).stop(instance_id)


def _inst_md_cell(text: str) -> str:
    """将单元格文本压成单行并转义 ``|``，供 GFM 表格渲染。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _short_project_dir_label(project_dir: str) -> str:
    """表格用短项目目录标签。"""
    norm = os.path.normpath(project_dir)
    base = os.path.basename(norm) or norm
    parent = os.path.basename(os.path.dirname(norm))
    if parent and parent not in (".", ".."):
        return f"{parent}/{base}"
    return base


def _workspace_label(inst: dict[str, Any]) -> str:
    """表格用 workspace 标签（优先 project_key，否则短路径）。"""
    key = inst.get("project_key")
    if key:
        return f"projects/{key}"
    state_dir = inst.get("project_state_dir")
    if state_dir:
        norm = os.path.normpath(str(state_dir))
        base = os.path.basename(norm)
        parent = os.path.basename(os.path.dirname(norm))
        if parent == "projects" and base:
            return f"projects/{base}"
        return _short_state_dir_label(norm)
    return "?"


def format_instances_markdown(instances: list[dict[str, Any]]) -> str:
    """运行实例列表的 GFM 表格（飞书友好）。"""
    from miniagent.infrastructure.paths import resolve_registry_state_dir

    registry = resolve_registry_state_dir()
    if not instances:
        return f"📭 暂无运行实例\n\n注册表: `{registry}`"

    state_dirs = {str(i.get("state_dir", registry)) for i in instances}
    multi_root = len(state_dirs) > 1

    lines = [
        "## 运行实例",
        "",
        f"注册表: `{registry}`",
        "",
        "> cli=仅 CLI，both=CLI+飞书",
        "",
        "| ID | PID | 模式 | 项目目录 | Workspace | 启动时间 | 会话数 | 主机 |"
        + (" 状态目录 |" if multi_root else "")
        + " 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |"
        + (" --- |" if multi_root else "")
        + " --- |",
    ]

    my_pid = os.getpid()
    for inst in instances:
        marker = "当前" if inst["pid"] == my_pid else ""
        sid = inst["instance_id"]
        pid = inst["pid"]
        mode = inst.get("mode", "?")
        proj = _short_project_dir_label(_meta_project_dir(inst))
        ws = _workspace_label(inst)
        start = str(inst.get("start_time", "?"))[:19]
        sessions = len(inst.get("active_sessions", []))
        host = inst.get("hostname", "?")
        row = (
            f"| {sid} | {pid} | {_inst_md_cell(str(mode))} | {_inst_md_cell(proj)} | "
            f"{_inst_md_cell(ws)} | {_inst_md_cell(start)} | {sessions} | "
            f"{_inst_md_cell(str(host))} |"
        )
        if multi_root:
            sd = _short_state_dir_label(str(inst.get("state_dir", registry)), canonical=registry)
            row += f" {_inst_md_cell(sd)} |"
        row += f" {_inst_md_cell(marker)} |"
        lines.append(row)
    return "\n".join(lines) + "\n"


def format_instances_table(instances: list[dict[str, Any]]) -> str:
    """格式化为表格文本。"""
    from miniagent.infrastructure.paths import resolve_registry_state_dir

    registry = resolve_registry_state_dir()
    if not instances:
        return f"📭 暂无运行实例\n\n  注册表: {registry}\n"

    state_dirs = {str(i.get("state_dir", registry)) for i in instances}
    multi_root = len(state_dirs) > 1

    lines = []
    lines.append("📋 运行实例列表:\n")
    lines.append(f"  注册表: {registry}\n")
    if multi_root:
        lines.append(
            f"  {'ID':<6} {'PID':<8} {'模式':<8} {'项目目录':<18} {'Workspace':<22} "
            f"{'启动时间':<22} {'会话数':<6} {'主机':<12} {'状态目录'}"
        )
        lines.append("  " + "-" * 128)
    else:
        lines.append(
            f"  {'ID':<6} {'PID':<8} {'模式':<8} {'项目目录':<18} {'Workspace':<22} "
            f"{'启动时间':<22} {'会话数':<6} {'主机'}"
        )
        lines.append("  " + "-" * 108)
    lines.append("  （cli=仅 CLI，both=CLI+飞书）")

    my_pid = os.getpid()
    for inst in instances:
        marker = " ← 当前" if inst["pid"] == my_pid else ""
        sid = inst["instance_id"]
        pid = inst["pid"]
        mode = inst.get("mode", "?")
        proj = _short_project_dir_label(_meta_project_dir(inst))
        ws = _workspace_label(inst)
        start = inst.get("start_time", "?")[:19]
        sessions = len(inst.get("active_sessions", []))
        host = inst.get("hostname", "?")
        if multi_root:
            sd = _short_state_dir_label(str(inst.get("state_dir", registry)), canonical=registry)
            lines.append(
                f"  #{sid:<5} {pid:<8} {mode:<8} {proj:<18} {ws:<22} {start:<22} "
                f"{sessions:<6} {host:<12} {sd}{marker}"
            )
        else:
            lines.append(
                f"  #{sid:<5} {pid:<8} {mode:<8} {proj:<18} {ws:<22} {start:<22} "
                f"{sessions:<6} {host}{marker}"
            )

    lines.append("")
    return "\n".join(lines)


def reset_instance_registry_for_tests() -> None:
    """清空 InstanceRegistry 缓存，仅供测试使用。"""
    global _default_registry
    _default_registry = None
    _clear_instance_list_caches()


__all__ = [
    "InstanceRegistry",
    "ProjectDirConflictError",
    "format_project_conflict_message",
    "register_instance",
    "update_instance_mode",
    "heartbeat",
    "unregister_instance",
    "list_instances",
    "list_instances_cached",
    "stop_instance_by_id",
    "find_alive_instance_for_project",
    "format_instances_table",
    "format_instances_markdown",
    "is_process_running",
    "is_process_running_async",
    "HEARTBEAT_TIMEOUT",
    "reset_instance_registry_for_tests",
]
