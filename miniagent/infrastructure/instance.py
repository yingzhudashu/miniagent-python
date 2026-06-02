"""Mini Agent Python — 多实例注册表

支持多个实例同时运行，每个实例独立注册、心跳、注销。

**存活与清理（重要）**

- 是否在列表中显示、是否删除磁盘目录，均以 **操作系统 PID 是否存在** 为准（``is_process_running``）。
- ``register()`` 在分配新 ``instance_id`` **之前** 会扫描并删除 PID 已失效的目录；**不会**向其它进程发送终止信号。
- ``heartbeat`` 文件仍会更新，便于人工排查；**不参与**「是否存活」的权威判定，避免心跳写入滞后导致误删仍在运行的实例注册信息。

运维向说明见 ``docs/INSTANCE_REGISTRY.md``。

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
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

# 配置默认值（直接读取环境变量，避免循环导入）
import os as _os_for_inst
HEARTBEAT_TIMEOUT = int(_os_for_inst.environ.get("MINIAGENT_HEARTBEAT_TIMEOUT", "30"))
INSTANCE_CACHE_TTL = float(_os_for_inst.environ.get("MINIAGENT_INSTANCE_CACHE_TTL", "30.0"))

_logger = get_logger(__name__)

# 实例 mode 仅两种：始终有 CLI 主循环；both 表示同进程已启用飞书连接
_VALID_INSTANCE_MODES = frozenset({"cli", "both"})


def _validate_instance_mode(mode: str) -> None:
    """校验实例 mode 属于 ``cli`` / ``both``，否则抛 ``ValueError``。"""
    if mode not in _VALID_INSTANCE_MODES:
        raise ValueError(f"instance mode must be 'cli' or 'both', got {mode!r}")


def _ensure_instances_dir(state_dir: str) -> Path:
    """确保实例注册目录存在。"""
    inst_dir = Path(state_dir) / "instances"
    inst_dir.mkdir(parents=True, exist_ok=True)
    return inst_dir


def _get_state_dir(state_dir: str | None = None) -> str:
    """获取状态目录。"""
    return state_dir or get_config("paths.state_dir", os.path.join(os.getcwd(), "workspaces"))


def is_process_running(pid: int) -> bool:
    """检测 PID 对应的进程是否仍在运行。"""
    try:
        if sys.platform == "win32":
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                timeout=5,
                text=True,
            )
            return f'"{pid}"' in output
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


async def is_process_running_async(pid: int) -> bool:
    """异步检测 PID 对应的进程是否仍在运行（不阻塞事件循环）。

    用于异步上下文中的实例存活检查，避免 subprocess.check_output 阻塞。

    Args:
        pid: 进程 ID

    Returns:
        进程是否仍在运行
    """
    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                "tasklist",
                "/FI",
                f"PID eq {pid}",
                "/NH",
                "/FO",
                "CSV",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return f'"{pid}"' in stdout.decode("utf-8", errors="replace")
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _next_instance_id(inst_dir: Path) -> int:
    """获取下一个实例 ID（自增）。"""
    max_id = 0
    for entry in inst_dir.iterdir():
        if entry.is_dir() and entry.name.isdigit():
            max_id = max(max_id, int(entry.name))
    return max_id + 1


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
        state_dir: 状态根目录；默认 ``MINIAGENT_PATHS_STATE_DIR`` 或仓库下 ``workspaces``。
        pid_checker: 可注入的 PID 存活探测（测试用）。
        """
        self._state_dir = _get_state_dir(state_dir)
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
        _validate_instance_mode(mode)
        # 新实例领取 ID 前清扫僵尸目录，使 ``--stop``/列表与磁盘一致且不误占号
        self._cleanup_dead_registered_instances()
        inst_id = _next_instance_id(self._inst_dir)
        my_dir = self._inst_dir / str(inst_id)
        my_dir.mkdir(exist_ok=True)

        self._meta = {
            "pid": os.getpid(),
            "instance_id": inst_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "active_sessions": active_sessions or [],
            "hostname": socket.gethostname(),
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

    def list_all(self) -> list[dict[str, Any]]:
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
            except Exception:
                pass
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
        except Exception:
            pass

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
            except Exception:
                pass
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
        except Exception:
            pass

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
            except Exception:
                pass

# ─── 模块级便捷函数 ───

_default_registry: InstanceRegistry | None = None

# ── 性能优化：实例列表缓存 ──
_instance_list_cache: tuple[float, list[dict[str, Any]]] | None = None
# 使用 constants.py 中定义的 TTL


def get_registry(state_dir: str | None = None) -> InstanceRegistry:
    """获取或创建默认实例注册表。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = InstanceRegistry(state_dir)
    return _default_registry


def register_instance(
    mode: str = "cli",
    active_sessions: list[str] | None = None,
    state_dir: str | None = None,
) -> dict[str, Any]:
    """注册当前实例。mode 仅 ``cli`` 或 ``both``（CLI + 飞书）。"""
    # 清除缓存（注册后列表会变化）
    global _instance_list_cache
    _instance_list_cache = None
    return get_registry(state_dir).register(mode, active_sessions)


def update_instance_mode(mode: str, state_dir: str | None = None) -> None:
    """更新当前进程已注册实例的 mode（供飞书 start/stop 同步 meta）。"""
    get_registry(state_dir).update_mode(mode)


def heartbeat(state_dir: str | None = None) -> None:
    """更新当前实例心跳。"""
    get_registry(state_dir).heartbeat()


def unregister_instance(state_dir: str | None = None) -> None:
    """注销当前实例。"""
    # 清除缓存（注销后列表会变化）
    global _instance_list_cache
    _instance_list_cache = None
    get_registry(state_dir).unregister()


def list_instances(state_dir: str | None = None) -> list[dict[str, Any]]:
    """列出所有存活实例。"""
    return get_registry(state_dir).list_all()


def list_instances_cached(state_dir: str | None = None) -> list[dict[str, Any]]:
    """列出所有存活实例（带缓存，性能优化）。

    缓存 5 秒有效，避免频繁调用时的目录遍历开销。
    注册/注销操作会自动清除缓存。

    Args:
        state_dir: 状态目录

    Returns:
        存活实例列表
    """
    global _instance_list_cache
    now = time.time()
    if _instance_list_cache is not None and now - _instance_list_cache[0] < INSTANCE_CACHE_TTL:
        return _instance_list_cache[1]
    result = list_instances(state_dir)
    _instance_list_cache = (now, result)
    return result


def stop_instance_by_id(instance_id: int, state_dir: str | None = None) -> dict[str, Any]:
    """停止指定实例。"""
    # 清除缓存（停止后列表会变化）
    global _instance_list_cache
    _instance_list_cache = None
    return get_registry(state_dir).stop(instance_id)


def _inst_md_cell(text: str) -> str:
    """将单元格文本压成单行并转义 ``|``，供 GFM 表格渲染。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("|", "\\|").replace("\n", " ").strip()


def format_instances_markdown(instances: list[dict[str, Any]]) -> str:
    """运行实例列表的 GFM 表格（飞书友好）。"""
    if not instances:
        return "📭 暂无运行实例"

    lines = [
        "## 运行实例",
        "",
        "> cli=仅 CLI，both=CLI+飞书",
        "",
        "| ID | PID | 模式 | 启动时间 | 会话数 | 主机 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    my_pid = os.getpid()
    for inst in instances:
        marker = "当前" if inst["pid"] == my_pid else ""
        sid = inst["instance_id"]
        pid = inst["pid"]
        mode = inst.get("mode", "?")
        start = str(inst.get("start_time", "?"))[:19]
        sessions = len(inst.get("active_sessions", []))
        host = inst.get("hostname", "?")
        lines.append(
            f"| {sid} | {pid} | {_inst_md_cell(str(mode))} | {_inst_md_cell(start)} | {sessions} | "
            f"{_inst_md_cell(str(host))} | {_inst_md_cell(marker)} |"
        )
    return "\n".join(lines) + "\n"


def format_instances_table(instances: list[dict[str, Any]]) -> str:
    """格式化为表格文本。"""
    if not instances:
        return "📭 暂无运行实例"

    lines = []
    lines.append("📋 运行实例列表:\n")
    lines.append(f"  {'ID':<6} {'PID':<8} {'模式':<8} {'启动时间':<22} {'会话数':<6} {'主机'}")
    lines.append("  " + "-" * 65)
    lines.append("  （cli=仅 CLI，both=CLI+飞书）")

    my_pid = os.getpid()
    for inst in instances:
        marker = " ← 当前" if inst["pid"] == my_pid else ""
        sid = inst["instance_id"]
        pid = inst["pid"]
        mode = inst.get("mode", "?")
        start = inst.get("start_time", "?")[:19]
        sessions = len(inst.get("active_sessions", []))
        host = inst.get("hostname", "?")
        lines.append(f"  #{sid:<5} {pid:<8} {mode:<8} {start:<22} {sessions:<6} {host}{marker}")

    lines.append("")
    return "\n".join(lines)


__all__ = [
    "InstanceRegistry",
    "register_instance",
    "update_instance_mode",
    "heartbeat",
    "unregister_instance",
    "list_instances",
    "list_instances_cached",
    "stop_instance_by_id",
    "format_instances_table",
    "format_instances_markdown",
    "is_process_running",
    "is_process_running_async",
    "HEARTBEAT_TIMEOUT",
]
