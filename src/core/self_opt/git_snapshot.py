"""Git 分支快照管理器 (Phase 5.4 升级)

Self-Optimization 子系统的 Git 快照组件。提供 Git 快照和回滚功能。

Phase 5.4 升级：
- create_snapshot 改用新分支（git checkout -b self-opt/<timestamp>）而非 stash
- revert_to_snapshot 改为 git checkout main && git branch -D <branch>
- 避免 reset --hard 导致的未提交改动丢失
- 增加分支隔离，不影响主分支

核心功能：
1. create_snapshot: 创建 Git 快照分支，保存当前状态
2. revert_to_snapshot: 删除快照分支，回退到主分支
3. finalize_snapshot: 合并快照分支到主分支，清理
4. is_in_git_repo: 检查当前目录是否在 Git 仓库中

工作流程：
    优化前: create_snapshot() → 创建分支 self-opt/<timestamp>
      优化成功: finalize_snapshot() → 合并到主分支
      优化失败: revert_to_snapshot() → 删除分支，回到主分支

设计原则：
- 使用原生 git 命令，不依赖外部库
- 通过分支隔离，保护用户未提交的改动
- 回滚操作只影响优化相关的提交，不影响用户代码
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# 分支前缀
BRANCH_PREFIX = "self-opt"


def _generate_branch_name() -> str:
    """生成快照分支名。"""
    import datetime

    now = datetime.datetime.now(datetime.UTC)
    ts = now.isoformat(timespec="seconds").replace(":", "-")
    return f"{BRANCH_PREFIX}/{ts}"


def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    """执行 Git 命令。

    使用 subprocess.run 执行 git 命令，避免 shell 注入风险。

    Args:
        args: Git 命令参数列表。
        cwd: 工作目录。

    Returns:
        (exit_code, stdout, stderr)
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False,
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


@dataclass
class SnapshotInfo:
    """快照信息。"""

    branch_name: str
    created_at: str
    base_commit: str
    is_current_branch: bool = True


async def is_in_git_repo(cwd: str) -> bool:
    """检查当前目录是否在 Git 仓库中。

    通过执行 `git rev-parse --git-dir` 来判断。

    Args:
        cwd: 要检查的目录。

    Returns:
        是否在 Git 仓库中。
    """
    code, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
    return code == 0


async def has_uncommitted_changes(cwd: str) -> bool:
    """检查是否有未提交的改动。

    通过执行 `git status --porcelain` 来判断。

    Args:
        cwd: 工作目录。

    Returns:
        是否有未提交的改动。
    """
    code, stdout, _ = _run_git(["status", "--porcelain"], cwd)
    return code == 0 and len(stdout.strip()) > 0


async def get_current_branch(cwd: str) -> str | None:
    """获取当前分支名。"""
    code, stdout, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return stdout.strip() if code == 0 else None


async def get_current_head(cwd: str) -> str | None:
    """获取当前 HEAD 的 commit hash。"""
    code, stdout, _ = _run_git(["rev-parse", "HEAD"], cwd)
    return stdout.strip() if code == 0 else None


async def _get_default_branch(cwd: str) -> str | None:
    """获取默认分支名（main 或 master）。"""
    code, _, _ = _run_git(["rev-parse", "--verify", "main"], cwd)
    if code == 0:
        return "main"
    code, _, _ = _run_git(["rev-parse", "--verify", "master"], cwd)
    if code == 0:
        return "master"
    return None


async def create_snapshot(cwd: str, msg: str = "") -> SnapshotInfo | None:
    """创建 Git 快照（Phase 5.4：使用分支隔离）。

    Phase 5.4 升级：
    - 旧方案：stash + commit + reset --hard（风险高，可能丢失改动）
    - 新方案：创建新分支 self-opt/<timestamp>，在分支上提交，不影响主分支

    Args:
        cwd: 工作目录。
        msg: 快照描述（作为分支描述的一部分）。

    Returns:
        快照信息，失败时返回 None。
    """
    try:
        # Step 1: 获取当前 HEAD
        head = await get_current_head(cwd)
        if not head:
            return None

        base_commit = head
        branch_name = _generate_branch_name()

        # Step 2: 如果有未提交的改动，先 stash 保护
        has_changes = await has_uncommitted_changes(cwd)
        if has_changes:
            code, _, _ = _run_git(
                ["stash", "push", "-m", f"self-opt-stash-{__import__('time').time_ns()}"],
                cwd,
            )
            if code != 0:
                print("[git-snapshot] Stash 失败，但继续创建快照分支")

        # Step 3: 创建快照分支
        code, _, stderr = _run_git(["checkout", "-b", branch_name], cwd)
        if code != 0:
            print(f"[git-snapshot] 创建分支失败: {stderr}")
            return None

        return SnapshotInfo(
            branch_name=branch_name,
            created_at=__import__("datetime").datetime.now(datetime.UTC).isoformat(),
            base_commit=base_commit,
            is_current_branch=True,
        )
    except Exception:
        return None


async def revert_to_snapshot(
    cwd: str,
    snapshot_info: SnapshotInfo | str,
    base_branch: str | None = None,
) -> tuple[bool, str | None]:
    """回滚到快照（Phase 5.4：删除分支而非 reset --hard）。

    Args:
        cwd: 工作目录。
        snapshot_info: 快照信息或分支名。
        base_branch: 要回退到的分支名（默认 main 或 master）。

    Returns:
        (是否成功, 错误信息)
    """
    try:
        branch_name = (
            snapshot_info if isinstance(snapshot_info, str) else snapshot_info.branch_name
        )

        # 验证分支存在
        code, _, _ = _run_git(["rev-parse", "--verify", branch_name], cwd)
        if code != 0:
            return False, "快照分支不存在"

        # 确定要回退到的目标分支
        target = base_branch or await _get_default_branch(cwd) or "main"

        # 检查目标分支是否存在
        code, _, _ = _run_git(["rev-parse", "--verify", target], cwd)
        if code != 0:
            return False, f"目标分支 {target} 不存在"

        # 切换到目标分支
        code, _, stderr = _run_git(["checkout", target], cwd)
        if code != 0:
            return False, f"切换到 {target} 失败: {stderr}"

        # 删除快照分支
        code, _, stderr = _run_git(["branch", "-D", branch_name], cwd)
        if code != 0:
            print(f"[git-snapshot] 删除快照分支失败: {stderr}")

        return True, None
    except Exception as e:
        return False, str(e)


async def finalize_snapshot(
    cwd: str,
    snapshot_info: SnapshotInfo | str,
    commit_msg: str,
    base_branch: str | None = None,
) -> bool:
    """确认快照有效（合并到主分支）。

    Phase 5.4 升级：
    - 旧方案：commit --amend 修改 message
    - 新方案：合并快照分支到主分支，删除快照分支

    Args:
        cwd: 工作目录。
        snapshot_info: 快照信息或分支名。
        commit_msg: 合并提交 message。
        base_branch: 目标分支（默认 main 或 master）。

    Returns:
        是否成功。
    """
    try:
        branch_name = (
            snapshot_info if isinstance(snapshot_info, str) else snapshot_info.branch_name
        )

        # 确定目标分支
        target = base_branch or await _get_default_branch(cwd) or "main"

        # 确保在目标分支上
        current = await get_current_branch(cwd)
        if current != target:
            code, _, _ = _run_git(["checkout", target], cwd)
            if code != 0:
                return False

        # 合并快照分支到目标分支
        code, _, stderr = _run_git(
            ["merge", "--no-ff", branch_name, "-m", commit_msg, "--no-verify"],
            cwd,
        )
        if code != 0:
            print(f"[git-snapshot] 合并失败: {stderr}")
            return False

        # 删除快照分支
        _run_git(["branch", "-d", branch_name], cwd)

        return True
    except Exception:
        return False


async def list_snapshots(cwd: str) -> list[str]:
    """列出所有 self-opt 快照分支。"""
    try:
        code, stdout, _ = _run_git(["branch", "--list", f"{BRANCH_PREFIX}/*"], cwd)
        if code != 0:
            return []
        return [b.strip() for b in stdout.strip().split("\n") if b.strip()]
    except Exception:
        return []


async def cleanup_snapshots(cwd: str, keep_recent: int = 0) -> int:
    """清理所有过期的 self-opt 分支。

    Args:
        cwd: 工作目录。
        keep_recent: 保留最近 N 个快照（默认 0，全部删除）。

    Returns:
        删除的分支数量。
    """
    try:
        branches = await list_snapshots(cwd)
        if len(branches) <= keep_recent:
            return 0

        # 按创建时间排序（分支名包含时间戳）
        sorted_branches = sorted(branches, reverse=True)
        to_delete = sorted_branches[keep_recent:] if keep_recent > 0 else sorted_branches

        deleted = 0
        for branch in to_delete:
            code, _, _ = _run_git(["branch", "-D", branch], cwd)
            if code == 0:
                deleted += 1
        return deleted
    except Exception:
        return 0


async def get_recent_commits(cwd: str, n: int = 10) -> list[dict[str, str]]:
    """获取最近提交历史。

    Args:
        cwd: 工作目录。
        n: 返回的提交数量（默认 10）。

    Returns:
        提交历史列表。
    """
    try:
        code, stdout, _ = _run_git(
            ["log", f"--max-count={n}", "--pretty=format:%H|%s|%ai"],
            cwd,
        )
        if code != 0:
            return []

        results = []
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 2)
            results.append({
                "hash": parts[0] if len(parts) > 0 else "",
                "message": parts[1] if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
            })
        return results
    except Exception:
        return []
