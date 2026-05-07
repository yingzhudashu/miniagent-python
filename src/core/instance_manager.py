"""Mini Agent Python — 单实例管理

确保 mini-agent 同一时间只有一个运行的实例。
类似 OpenClaw 的 instance guard 机制。

工作原理：
1. 启动时创建 PID 文件：state/instance.pid
2. 如果 PID 文件已存在，检查对应进程是否存活
3. 进程存活 → 拒绝启动（另一实例正在运行）
4. 进程死亡 → 清理过期 PID 文件，允许启动
5. 退出时自动删除 PID 文件

适用于 Windows / macOS / Linux。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from src.core.logger import get_logger

_logger = get_logger(__name__)


def _ensure_state_dir(state_dir: str) -> None:
    """确保状态目录存在

    Args:
        state_dir: 状态目录路径
    """
    os.makedirs(state_dir, exist_ok=True)


def _is_process_running(pid: int) -> bool:
    """检测 PID 对应的进程是否仍在运行

    Windows: 使用 tasklist
    Unix: 使用 os.kill(pid, 0)

    Args:
        pid: 进程 ID

    Returns:
        True 如果进程仍在运行
    """
    try:
        if sys.platform == "win32":
            # Windows: 用 tasklist 检查进程
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                timeout=5,
                text=True,
            )
            return f'"{pid}"' in output
        else:
            # Unix: kill -0 不发送信号，只检查进程是否存在
            os.kill(pid, 0)
            return True
    except Exception:
        return False


class InstanceManager:
    """单实例管理器

    管理 PID 文件的创建、检查和清理。

    Example:
        mgr = InstanceManager(state_dir="./state")
        result = mgr.try_acquire()
        if result["success"]:
            print("启动成功")
        else:
            print(f"已有实例运行: PID={result['existing_pid']}")
    """

    def __init__(self, state_dir: str = "state") -> None:
        """创建实例管理器

        Args:
            state_dir: 状态存储目录
        """
        self._state_dir = state_dir
        self._pid_file = os.path.join(state_dir, "instance.pid")

    def try_acquire(self) -> dict:
        """尝试获取单实例锁

        Returns:
            {"success": True} 或 {"success": False, "existing_pid": int}
        """
        _ensure_state_dir(self._state_dir)

        # 读取现有 PID 文件
        if os.path.exists(self._pid_file):
            try:
                with open(self._pid_file, "r") as f:
                    raw = f.read().strip()
                existing_pid = int(raw)

                if _is_process_running(existing_pid):
                    return {"success": False, "existing_pid": existing_pid}

                # PID 文件存在但进程已死
                _logger.warning(
                    "检测到过期 PID 文件 (PID=%d)，进程已不存在，清理中...",
                    existing_pid,
                )
                os.unlink(self._pid_file)
            except (ValueError, OSError):
                # PID 文件损坏，删除重建
                _logger.warning("PID 文件读取失败，清理中...")
                try:
                    os.unlink(self._pid_file)
                except OSError:
                    pass

        # 写入当前 PID
        with open(self._pid_file, "w") as f:
            f.write(str(os.getpid()))
        return {"success": True}

    def force_acquire(self) -> dict:
        """强制获取单实例锁（杀死已有进程）

        Returns:
            {"success": True} 或 {"success": False, "reason": str}
        """
        _ensure_state_dir(self._state_dir)

        if not os.path.exists(self._pid_file):
            with open(self._pid_file, "w") as f:
                f.write(str(os.getpid()))
            return {"success": True}

        try:
            with open(self._pid_file, "r") as f:
                raw = f.read().strip()
            existing_pid = int(raw)

            if not _is_process_running(existing_pid):
                with open(self._pid_file, "w") as f:
                    f.write(str(os.getpid()))
                return {"success": True}

            # 尝试终止已有进程
            _logger.info("正在终止旧实例 (PID=%d)...", existing_pid)
            try:
                if sys.platform == "win32":
                    subprocess.check_output(
                        ["taskkill", "/PID", str(existing_pid), "/F"],
                        timeout=10,
                    )
                else:
                    os.kill(existing_pid, 9)  # SIGKILL
                    # 等待进程退出
                    for _ in range(50):
                        if not _is_process_running(existing_pid):
                            break
                        time.sleep(0.1)
            except Exception:
                return {
                    "success": False,
                    "reason": f"无法终止 PID={existing_pid} 的进程",
                }

            # 清理并写入新 PID
            try:
                os.unlink(self._pid_file)
            except OSError:
                pass
            with open(self._pid_file, "w") as f:
                f.write(str(os.getpid()))
            return {"success": True}

        except Exception as e:
            return {"success": False, "reason": str(e)}

    def release(self) -> None:
        """释放单实例锁（退出时调用）"""
        try:
            if os.path.exists(self._pid_file):
                os.unlink(self._pid_file)
        except OSError:
            pass  # 忽略清理失败

    def stop(self) -> dict:
        """停止正在运行的实例

        Returns:
            {"success": True} 或 {"success": False, "reason": str}
        """
        _ensure_state_dir(self._state_dir)

        if not os.path.exists(self._pid_file):
            return {
                "success": False,
                "reason": "没有运行中的实例（PID 文件不存在）",
            }

        try:
            with open(self._pid_file, "r") as f:
                raw = f.read().strip()
            existing_pid = int(raw)

            if not _is_process_running(existing_pid):
                os.unlink(self._pid_file)
                return {
                    "success": False,
                    "reason": f"PID={existing_pid} 的进程已不存在，已清理残留文件",
                }

            # 终止进程
            _logger.info("正在停止 Mini Agent (PID=%d)...", existing_pid)
            try:
                if sys.platform == "win32":
                    subprocess.check_output(
                        ["taskkill", "/PID", str(existing_pid), "/F"],
                        timeout=10,
                    )
                else:
                    os.kill(existing_pid, 15)  # SIGTERM
                    for _ in range(50):
                        if not _is_process_running(existing_pid):
                            break
                        time.sleep(0.1)
            except Exception as e:
                return {
                    "success": False,
                    "reason": f"无法终止 PID={existing_pid} 的进程: {e}",
                }

            # 清理 PID 文件
            try:
                os.unlink(self._pid_file)
            except OSError:
                pass
            _logger.info("Mini Agent 已停止")
            return {"success": True}

        except Exception as e:
            return {"success": False, "reason": str(e)}


# ─── 模块级便捷函数 ───

_default_mgr = InstanceManager()


def try_acquire_instance() -> dict:
    """尝试获取单实例锁（便捷函数）。"""
    return _default_mgr.try_acquire()


def force_acquire_instance() -> dict:
    """强制获取单实例锁（便捷函数）。"""
    return _default_mgr.force_acquire()


def release_instance() -> None:
    """释放单实例锁（便捷函数）。"""
    _default_mgr.release()


def stop_instance() -> dict:
    """停止正在运行的实例（便捷函数）。"""
    return _default_mgr.stop()


__all__ = [
    "InstanceManager",
    "try_acquire_instance",
    "force_acquire_instance",
    "release_instance",
    "stop_instance",
]
