"""Global SQLite registry for concurrently running MiniAgent processes."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from miniagent.agent.constants import INSTANCE_CACHE_TTL, INSTANCE_HEARTBEAT_TIMEOUT
from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import ERROR_PREFIX
from miniagent.assistant.infrastructure.process_utils import (
    is_process_running,
    is_process_running_async,
)
from miniagent.assistant.state.registry import (
    ProcessInstance,
    ProcessInstanceConflictError,
    ProcessInstanceStore,
)

_logger = get_logger(__name__)

# 实例 mode 仅两种：始终有 CLI 主循环；both 表示同进程已启用飞书连接
_VALID_INSTANCE_MODES = frozenset({"cli", "both"})


class ProjectDirConflictError(Exception):
    """同一项目目录已有存活实例时抛出。"""

    def __init__(self, existing_meta: dict[str, Any]) -> None:
        """Args:
            existing_meta: 冲突实例的当前注册表行（用于格式化提示）。
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
        from miniagent.assistant.infrastructure.paths import (
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


def _get_registry_state_dir(state_dir: str | None = None) -> str:
    """获取实例注册表状态根目录。"""
    from miniagent.assistant.infrastructure.paths import resolve_registry_state_dir

    return state_dir or resolve_registry_state_dir()


def _meta_project_dir(meta: dict[str, Any]) -> str:
    """Return the normalized project directory from current registry metadata."""
    from miniagent.assistant.infrastructure.paths import normalize_project_dir

    return normalize_project_dir(str(meta["project_dir"]))


def find_alive_instance_for_project(
    project_dir: str,
    *,
    state_dir: str | None = None,
    pid_checker: Any = None,
) -> dict[str, Any] | None:
    """查找占用指定项目目录的存活实例（无则返回 ``None``）。"""
    from miniagent.assistant.infrastructure.paths import normalize_project_dir, paths_equal

    target = normalize_project_dir(project_dir)
    checker = pid_checker or is_process_running
    reg = InstanceRegistry(state_dir=state_dir, pid_checker=checker)
    for inst in reg.list_all():
        if paths_equal(_meta_project_dir(inst), target):
            return inst
    return None


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


# ============================================================================
# InstanceRegistry
# ============================================================================


class InstanceRegistry:
    """Register, discover, update, and stop live processes through SQLite."""

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
        self._store = ProcessInstanceStore(self._state_dir)
        self._my_id: int | None = None
        self._meta: dict[str, Any] = {}
        self._pid_checker = pid_checker or is_process_running

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _stale_before_ms(self) -> int:
        return self._now_ms() - int(INSTANCE_HEARTBEAT_TIMEOUT * 1000)

    @staticmethod
    def _to_meta(instance: ProcessInstance) -> dict[str, Any]:
        return {
            "pid": instance.pid,
            "instance_id": instance.instance_id,
            "start_time": datetime.fromtimestamp(
                instance.started_at_ms / 1000, timezone.utc
            ).isoformat(),
            "mode": instance.mode,
            "active_sessions": list(instance.active_sessions),
            "hostname": instance.hostname,
            "project_dir": instance.project_dir,
            "project_key": instance.project_key,
            "project_state_dir": instance.project_state_dir,
            "heartbeat_at_ms": instance.heartbeat_at_ms,
        }

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
        from miniagent.assistant.infrastructure.paths import (
            resolve_project_dir,
            resolve_project_key,
            resolve_project_state_dir,
        )

        project_dir = resolve_project_dir()
        project_state_dir = resolve_project_state_dir()
        project_key = resolve_project_key(project_dir)

        _validate_instance_mode(mode)
        try:
            instance = self._store.register(
                project_dir=project_dir,
                project_key=project_key,
                project_state_dir=project_state_dir,
                pid=os.getpid(),
                mode=mode,
                active_sessions=active_sessions or (),
                hostname=socket.gethostname(),
                now_ms=self._now_ms(),
                alive_pid=self._pid_checker,
                stale_before_ms=self._stale_before_ms(),
            )
        except ProcessInstanceConflictError as error:
            raise ProjectDirConflictError(self._to_meta(error.existing)) from error

        self._my_id = instance.instance_id
        self._meta = self._to_meta(instance)

        _logger.info(
            "实例已注册: #%d (PID=%d, mode=%s)",
            instance.instance_id,
            os.getpid(),
            mode,
        )
        return dict(self._meta)

    def heartbeat(self) -> None:
        """更新当前实例心跳。"""
        if self._my_id is not None:
            now_ms = self._now_ms()
            if self._store.heartbeat(self._my_id, now_ms):
                self._meta["heartbeat_at_ms"] = now_ms

    def unregister(self) -> None:
        """注销当前实例（退出时调用）。"""
        if self._my_id is not None:
            self._store.delete(self._my_id)
            _logger.info("实例已注销: #%d", self._my_id)
        self._my_id = None
        self._meta = {}

    # ─── 查询 ───

    def list_all(self, *, attach_state_dir: bool = False) -> list[dict[str, Any]]:
        """列出所有存活实例。

        Returns:
            存活实例元数据列表，按 instance_id 排序。
        """
        instances = self._store.list_live(
            alive_pid=self._pid_checker,
            stale_before_ms=self._stale_before_ms(),
        )
        results = []
        for instance in instances:
            meta = self._to_meta(instance)
            meta["alive"] = True
            if attach_state_dir:
                meta["state_dir"] = self._state_dir
            results.append(meta)
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
        instance = self._store.get(instance_id)
        if instance is None:
            return {"success": False, "reason": f"实例 #{instance_id} 不存在"}
        pid = instance.pid
        if not is_process_running(pid):
            self._store.delete(instance_id)
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

        self._store.delete(instance_id)

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
        instance = await asyncio.to_thread(self._store.get, instance_id)
        if instance is None:
            return {"success": False, "reason": f"实例 #{instance_id} 不存在"}
        pid = instance.pid
        if not await is_process_running_async(pid):
            await asyncio.to_thread(self._store.delete, instance_id)
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

        await asyncio.to_thread(self._store.delete, instance_id)

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
        self._store.update_sessions(self._my_id, active_sessions)

    def update_mode(self, mode: str) -> None:
        """更新当前实例的 mode（与飞书运行时开关同步）。"""
        if self._my_id is None:
            return
        _validate_instance_mode(mode)
        self._meta["mode"] = mode
        self._store.update_mode(self._my_id, mode)


# ─── 模块级便捷函数 ───

_default_registry: InstanceRegistry | None = None

# 实例列表按注册库路径隔离缓存，TTL 内复用同一存活性快照。
_instance_list_caches: dict[str | None, tuple[float, list[dict[str, Any]]]] = {}
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
) -> list[dict[str, Any]]:
    """列出指定注册表中的所有存活实例；省略时使用 canonical 注册表。"""
    reg = InstanceRegistry(state_dir=state_dir)
    return reg.list_all(attach_state_dir=True)


def list_instances_cached(
    state_dir: str | None = None,
) -> list[dict[str, Any]]:
    """列出所有存活实例，并在短 TTL 内复用同注册库快照。

    缓存 5 秒有效，按 ``state_dir`` 分键。
    注册/注销操作会自动清除缓存。

    Args:
        state_dir: 状态目录
    Returns:
        存活实例列表
    """
    global _instance_list_caches
    cache_key = state_dir
    now = time.time()
    cached = _instance_list_caches.get(cache_key)
    if cached is not None and now - cached[0] < INSTANCE_CACHE_TTL:
        return cached[1]
    result = list_instances(state_dir)
    _instance_list_caches[cache_key] = (now, result)
    return result


def stop_instance_by_id(
    instance_id: int,
    state_dir: str | None = None,
) -> dict[str, Any]:
    """停止指定注册表中的实例；省略 ``state_dir`` 时使用 canonical 注册表。"""
    _clear_instance_list_caches()
    return InstanceRegistry(state_dir=state_dir).stop(instance_id)


def format_instances_markdown(instances: list[dict[str, Any]]) -> str:
    """运行实例列表的 GFM 表格（飞书友好）。"""
    from miniagent.assistant.infrastructure.instance_render import (
        format_instances_markdown as render,
    )

    return render(instances)


def format_instances_table(instances: list[dict[str, Any]]) -> str:
    """格式化为等宽终端表格。"""
    from miniagent.assistant.infrastructure.instance_render import format_instances_table as render

    return render(instances)


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
    "reset_instance_registry_for_tests",
]
