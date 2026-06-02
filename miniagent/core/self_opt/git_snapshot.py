"""Self-optimization subsystem — Git 快照工具

在优化前后创建 Git 快照，确保可回滚。

功能：
- 检查是否在 Git 仓库中
- 创建快照（通过 Git tag 或 stash）
- 回滚到指定快照
- 检查未提交的变更

详见 ``docs/SELF_OPT.md``。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def is_in_git_repo(path: str | None = None) -> bool:
    """检查指定路径是否在 Git 仓库中。

    Args:
        path: 要检查的路径（默认当前目录）

    Returns:
        是否在 Git 仓库中
    """
    if path is None:
        path = os.getcwd()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


async def is_in_git_repo_async(path: str | None = None) -> bool:
    """异步检查指定路径是否在 Git 仓库中（不阻塞事件循环）。

    用于异步上下文中检查 Git 状态，避免 subprocess.run 阻塞。

    Args:
        path: 要检查的路径（默认当前目录）

    Returns:
        是否在 Git 仓库中
    """
    if path is None:
        path = os.getcwd()

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--is-inside-work-tree",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode == 0 and stdout.decode("utf-8", errors="replace").strip() == "true"
    except Exception:
        return False


def has_uncommitted_changes(path: str | None = None) -> bool:
    """检查是否有未提交的变更。

    Args:
        path: 项目路径

    Returns:
        是否有未提交变更
    """
    if path is None:
        path = os.getcwd()

    if not is_in_git_repo(path):
        return False

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


async def has_uncommitted_changes_async(path: str | None = None) -> bool:
    """异步检查是否有未提交的变更（不阻塞事件循环）。

    用于异步上下文中检查 Git 状态。

    Args:
        path: 项目路径

    Returns:
        是否有未提交变更
    """
    if path is None:
        path = os.getcwd()

    if not await is_in_git_repo_async(path):
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return bool(stdout.decode("utf-8", errors="replace").strip())
    except Exception:
        return False


def create_snapshot(
    message: str,
    *,
    path: str | None = None,
    include_unstaged: bool = True,
) -> dict[str, Any]:
    """创建 Git 快照。

    使用 `git stash` 创建快照，确保可回滚。

    Args:
        message: 快照描述
        path: 项目路径
        include_unstaged: 是否包含未暂存的变更

    Returns:
        快照信息 {"success": bool, "ref": str, "message": str}
               ref 为 stash@{N} 格式引用，可直接用于 pop/apply
    """
    if path is None:
        path = os.getcwd()

    if not is_in_git_repo(path):
        return {"success": False, "ref": "", "message": "不在 Git 仓库中"}

    try:
        # 先暂存所有变更
        if include_unstaged:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=path,
                capture_output=True,
                timeout=30,
            )

        # 创建 stash
        result = subprocess.run(
            ["git", "stash", "push", "-m", f"[snapshot] {message}"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            # 获取 stash 引用（使用 stash@{N} 格式，可直接用于 pop）
            stash_result = subprocess.run(
                ["git", "stash", "list", "-1", "--format=%gd"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            ref = stash_result.stdout.strip()
            return {"success": True, "ref": ref, "message": message}
        else:
            return {"success": False, "ref": "", "message": f"stash 失败: {result.stderr.strip()}"}

    except (subprocess.TimeoutExpired, OSError) as e:
        _logger.error("创建快照失败: %s", e)
        return {"success": False, "ref": "", "message": str(e)}


async def create_snapshot_async(
    message: str,
    *,
    path: str | None = None,
    include_unstaged: bool = True,
) -> dict[str, Any]:
    """异步创建 Git 快照（不阻塞事件循环）。

    用于异步上下文中创建快照，避免 subprocess.run 阻塞。

    Args:
        message: 快照描述
        path: 项目路径
        include_unstaged: 是否包含未暂存的变更

    Returns:
        快照信息 {"success": bool, "ref": str, "message": str}
    """
    if path is None:
        path = os.getcwd()

    if not await is_in_git_repo_async(path):
        return {"success": False, "ref": "", "message": "不在 Git 仓库中"}

    try:
        # 先暂存所有变更
        if include_unstaged:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "add",
                "-A",
                cwd=path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

        # 创建 stash
        proc = await asyncio.create_subprocess_exec(
            "git",
            "stash",
            "push",
            "-m",
            f"[snapshot] {message}",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            # 获取 stash 引用
            proc2 = await asyncio.create_subprocess_exec(
                "git",
                "stash",
                "list",
                "-1",
                "--format=%gd",
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await proc2.communicate()
            ref = stdout2.decode("utf-8", errors="replace").strip()
            return {"success": True, "ref": ref, "message": message}
        else:
            return {
                "success": False,
                "ref": "",
                "message": f"stash 失败: {stderr.decode('utf-8', errors='replace').strip()}",
            }

    except Exception as e:
        _logger.error("创建快照失败: %s", e)
        return {"success": False, "ref": "", "message": str(e)}


def rollback_snapshot(
    ref: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """回滚到指定快照。

    Args:
        ref: Git stash 引用
        path: 项目路径

    Returns:
        回滚结果 {"success": bool, "message": str}
    """
    if path is None:
        path = os.getcwd()

    if not is_in_git_repo(path):
        return {"success": False, "message": "不在 Git 仓库中"}

    try:
        # 弹出 stash（应用并删除）
        result = subprocess.run(
            ["git", "stash", "pop", ref],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            return {"success": True, "message": "回滚成功"}
        else:
            return {"success": False, "message": f"回滚失败: {result.stderr.strip()}"}

    except (subprocess.TimeoutExpired, OSError) as e:
        _logger.error("回滚失败: %s", e)
        return {"success": False, "message": str(e)}


async def rollback_snapshot_async(
    ref: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """异步回滚到指定快照（不阻塞事件循环）。

    用于异步上下文中回滚快照，避免 subprocess.run 阻塞。

    Args:
        ref: Git stash 引用
        path: 项目路径

    Returns:
        回滚结果 {"success": bool, "message": str}
    """
    if path is None:
        path = os.getcwd()

    if not await is_in_git_repo_async(path):
        return {"success": False, "message": "不在 Git 仓库中"}

    try:
        # 弹出 stash（应用并删除）
        proc = await asyncio.create_subprocess_exec(
            "git",
            "stash",
            "pop",
            ref,
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            return {"success": True, "message": "回滚成功"}
        else:
            return {
                "success": False,
                "message": f"回滚失败: {stderr.decode('utf-8', errors='replace').strip()}",
            }

    except Exception as e:
        _logger.error("回滚失败: %s", e)
        return {"success": False, "message": str(e)}


__all__ = [
    "is_in_git_repo",
    "is_in_git_repo_async",
    "has_uncommitted_changes",
    "has_uncommitted_changes_async",
    "create_snapshot",
    "create_snapshot_async",
    "rollback_snapshot",
    "rollback_snapshot_async",
]
