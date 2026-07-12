"""Self-optimization subsystem — 自动优化执行器

在安全约束下自动实施优化提案。

功能：
- 执行文件变更（创建/更新/删除）
- 运行验证测试
- 自动回滚（测试失败时）
- 生成优化结果报告

安全约束：
- 优先使用 Git stash 快照；无未提交变更时对涉及文件做内存备份
- 高风险提案需要 allow_high_risk 或 dry_run
- 无可执行内容（无 files 且无 test_cases）的提案跳过

详见 ``docs/SELF_OPT.md``。
"""

from __future__ import annotations

import asyncio
import os
import shlex

from miniagent.core.self_opt.git_snapshot import (
    create_snapshot_async,
    has_uncommitted_changes_async,
    is_in_git_repo_async,
    rollback_snapshot_async,
)
from miniagent.core.self_opt.types import (
    FileChange,
    OptimizationProposal,
    OptimizationResult,
    OptTestSummary,
)
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def _apply_file_change_sync(change: FileChange, root: str = "") -> bool:
    """应用单个文件变更。

    Args:
        change: 文件变更描述
        root: 项目根目录

    Returns:
        是否成功
    """
    target_path = os.path.join(root, change.path) if root else change.path

    try:
        if change.action == "create":
            # 确保父目录存在（无目录时跳过）
            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(change.content)
            _logger.info("创建文件: %s", change.path)

        elif change.action == "update":
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(change.content)
            _logger.info("更新文件: %s", change.path)

        elif change.action == "delete":
            if os.path.exists(target_path):
                os.remove(target_path)
                _logger.info("删除文件: %s", change.path)

        elif change.action == "rename":
            old_path = os.path.join(root, change.old_path) if root else change.old_path
            if os.path.exists(old_path):
                parent = os.path.dirname(target_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                os.rename(old_path, target_path)
                _logger.info("重命名: %s -> %s", change.old_path, change.path)

        return True

    except (OSError, PermissionError) as e:
        _logger.error("应用变更失败 [%s] %s: %s", change.action, change.path, e)
        return False


async def _apply_file_change(change: FileChange, root: str = "") -> bool:
    """Apply filesystem mutations in a worker so optimizer I/O cannot stall the loop."""
    return await asyncio.to_thread(_apply_file_change_sync, change, root)


def _backup_file(path: str) -> tuple[bool, bytes | None]:
    """备份文件内容；返回 (existed, content_or_none)。"""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return True, f.read()
    return False, None


def _restore_file_backup(path: str, existed: bool, content: bytes | None) -> None:
    """从备份恢复文件。"""
    if existed and content is not None:
        with open(path, "wb") as f:
            f.write(content)
    elif os.path.exists(path):
        os.remove(path)


def _collect_file_backups(
    proposal: OptimizationProposal,
    root: str,
) -> dict[str, tuple[bool, bytes | None]]:
    """收集提案涉及路径的当前内容备份。"""
    backups: dict[str, tuple[bool, bytes | None]] = {}
    for change in proposal.files:
        if change.action == "rename" and change.old_path:
            old_target = os.path.join(root, change.old_path) if root else change.old_path
            backups[old_target] = _backup_file(old_target)
        target = os.path.join(root, change.path) if root else change.path
        backups[target] = _backup_file(target)
    return backups


def _restore_file_backups(backups: dict[str, tuple[bool, bytes | None]]) -> None:
    """恢复所有文件备份。"""
    for path, (existed, content) in backups.items():
        try:
            _restore_file_backup(path, existed, content)
        except OSError as e:
            _logger.error("恢复文件备份失败 %s: %s", path, e)


async def _run_validation_tests(proposal: OptimizationProposal) -> OptTestSummary:
    """运行验证测试（异步版本，不阻塞事件循环）。

    Args:
        proposal: 优化提案

    Returns:
        测试摘要
    """
    summary = OptTestSummary()

    for tc in proposal.test_cases:
        if not tc.command.strip():
            continue
        summary.total += 1
        try:
            cmd_parts = shlex.split(tc.command, posix=os.name != "nt")
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                summary.passed += 1
            else:
                summary.failed += 1
                _logger.warning(
                    "测试失败 [%s]: %s",
                    tc.id,
                    stderr.decode("utf-8", errors="replace").strip(),
                )
        except Exception as e:
            summary.failed += 1
            _logger.error("测试执行失败 [%s]: %s", tc.id, e)

    return summary


async def apply_proposal(
    proposal: OptimizationProposal,
    *,
    root: str = "",
    auto_rollback: bool = True,
    dry_run: bool = False,
    allow_high_risk: bool = False,
) -> OptimizationResult:
    """应用优化提案。

    Args:
        proposal: 优化提案
        root: 项目根目录
        auto_rollback: 测试失败时自动回滚
        dry_run: 仅模拟执行（不实际修改文件）
        allow_high_risk: 已批准的高风险提案允许执行

    Returns:
        优化执行结果
    """
    result = OptimizationResult(proposal_id=proposal.id)

    if not proposal.files and not proposal.test_cases:
        result.status = "skipped"
        result.error = "提案无可执行的文件变更或验证测试"
        return result

    # 高风险检查
    if proposal.risk_level == "high" and not dry_run and not allow_high_risk:
        result.status = "skipped"
        result.error = "高风险提案需要先 /self-opt approve 后再执行"
        return result

    if not await is_in_git_repo_async(root):
        _logger.warning("不在 Git 仓库中，将使用文件级备份回滚")

    # Git stash 快照（有未提交变更时）
    snapshot_ref = ""
    if not dry_run and await is_in_git_repo_async(root) and await has_uncommitted_changes_async(root):
        snap_result = await create_snapshot_async(f"before-{proposal.id}", path=root)
        if snap_result["success"]:
            snapshot_ref = snap_result["ref"]
            _logger.info("创建前置快照: %s", snapshot_ref)

    file_backups = _collect_file_backups(proposal, root) if not dry_run else {}

    async def _rollback() -> str:
        suffix = ""
        if auto_rollback:
            if snapshot_ref:
                _logger.info("自动回滚到 Git 快照: %s", snapshot_ref)
                rollback_result = await rollback_snapshot_async(snapshot_ref, path=root)
                if rollback_result["success"]:
                    suffix = " (已回滚)"
                else:
                    suffix = f" (Git 回滚失败: {rollback_result['message']})"
            elif file_backups:
                _logger.info("自动回滚到文件备份")
                _restore_file_backups(file_backups)
                suffix = " (已回滚)"
        return suffix

    # Dry run 模式
    if dry_run:
        _logger.info("Dry run: 提案 %s 将应用 %d 个变更", proposal.id, len(proposal.files))
        result.status = "success"
        result.changes_applied = len(proposal.files)
        return result

    # 应用文件变更
    changes_applied = 0
    for change in proposal.files:
        success = await _apply_file_change(change, root)
        if success:
            changes_applied += 1
        else:
            result.status = "failed"
            result.error = f"变更失败: {change.path}"
            result.error += await _rollback()
            return result

    result.changes_applied = changes_applied

    # 运行验证测试
    if proposal.test_cases:
        test_summary = await _run_validation_tests(proposal)
        result.test_summary = test_summary

        if test_summary.failed > 0:
            result.status = "failed"
            result.error = f"{test_summary.failed}/{test_summary.total} 测试失败"
            result.error += await _rollback()
            return result

    result.status = "success"
    _logger.info("提案 %s 应用成功: %d 个变更", proposal.id, changes_applied)
    return result


async def run_auto_optimization(
    proposal: OptimizationProposal,
    *,
    root: str = "",
    auto_rollback: bool = True,
    dry_run: bool = False,
    allow_high_risk: bool = False,
) -> OptimizationResult:
    """运行自动优化（apply_proposal 的别名）。

    Args:
        proposal: 优化提案
        root: 项目根目录
        auto_rollback: 测试失败时自动回滚
        dry_run: 仅模拟执行
        allow_high_risk: 已批准的高风险提案允许执行

    Returns:
        优化执行结果
    """
    return await apply_proposal(
        proposal,
        root=root,
        auto_rollback=auto_rollback,
        dry_run=dry_run,
        allow_high_risk=allow_high_risk,
    )


__all__ = ["apply_proposal", "run_auto_optimization"]
