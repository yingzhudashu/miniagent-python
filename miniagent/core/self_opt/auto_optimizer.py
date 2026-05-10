"""Self-optimization subsystem — 自动优化执行器

在安全约束下自动实施优化提案。

功能：
- 执行文件变更（创建/更新/删除）
- 运行验证测试
- 自动回滚（测试失败时）
- 生成优化结果报告

安全约束：
- 仅在 Git 仓库中执行（确保可回滚）
- 高风险提案需要确认
- 每次变更前后创建快照
- 测试失败自动回滚
"""

from __future__ import annotations

import os

from miniagent.core.self_opt.types import (
    FileChange,
    OptimizationProposal,
    OptimizationResult,
    OptTestSummary,
)
from miniagent.core.self_opt.git_snapshot import (
    create_snapshot,
    has_uncommitted_changes,
    is_in_git_repo,
    rollback_snapshot,
)
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


async def _apply_file_change(change: FileChange, root: str = "") -> bool:
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
            # 确保父目录存在
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
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
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                os.rename(old_path, target_path)
                _logger.info("重命名: %s -> %s", change.old_path, change.path)

        return True

    except (OSError, PermissionError) as e:
        _logger.error("应用变更失败 [%s] %s: %s", change.action, change.path, e)
        return False


async def _run_validation_tests(proposal: OptimizationProposal) -> OptTestSummary:
    """运行验证测试。

    Args:
        proposal: 优化提案

    Returns:
        测试摘要
    """
    import subprocess

    summary = OptTestSummary()

    for tc in proposal.test_cases:
        summary.total += 1
        try:
            result = subprocess.run(
                tc.command.split(),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                summary.passed += 1
            else:
                summary.failed += 1
                _logger.warning("测试失败 [%s]: %s", tc.id, result.stderr.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            summary.failed += 1
            _logger.error("测试执行失败 [%s]: %s", tc.id, e)

    return summary


async def apply_proposal(
    proposal: OptimizationProposal,
    *,
    root: str = "",
    auto_rollback: bool = True,
    dry_run: bool = False,
) -> OptimizationResult:
    """应用优化提案。

    Args:
        proposal: 优化提案
        root: 项目根目录
        auto_rollback: 测试失败时自动回滚
        dry_run: 仅模拟执行（不实际修改文件）

    Returns:
        优化执行结果
    """
    result = OptimizationResult(proposal_id=proposal.id)

    # 高风险检查
    if proposal.risk_level == "high" and not dry_run:
        result.status = "skipped"
        result.error = "高风险提案需要人工确认"
        return result

    # 检查 Git 仓库
    if not is_in_git_repo(root):
        _logger.warning("不在 Git 仓库中，无法创建快照")

    # 创建快照（执行前）
    snapshot_ref = ""
    if not dry_run and is_in_git_repo(root) and has_uncommitted_changes(root):
        snap_result = create_snapshot(f"before-{proposal.id}", path=root)
        if snap_result["success"]:
            snapshot_ref = snap_result["ref"]
            _logger.info("创建前置快照: %s", snapshot_ref)

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

            # 自动回滚
            if auto_rollback and snapshot_ref:
                _logger.info("自动回滚到快照: %s", snapshot_ref)
                rollback_result = rollback_snapshot(snapshot_ref, path=root)
                if rollback_result["success"]:
                    result.error += " (已回滚)"
                else:
                    result.error += f" (回滚失败: {rollback_result['message']})"

            return result

    result.changes_applied = changes_applied

    # 运行验证测试
    if proposal.test_cases:
        test_summary = await _run_validation_tests(proposal)
        result.test_summary = test_summary

        if test_summary.failed > 0:
            result.status = "failed"
            result.error = f"{test_summary.failed}/{test_summary.total} 测试失败"

            # 自动回滚
            if auto_rollback and snapshot_ref:
                _logger.info("测试失败，自动回滚到快照: %s", snapshot_ref)
                rollback_result = rollback_snapshot(snapshot_ref, path=root)
                if rollback_result["success"]:
                    result.error += " (已回滚)"
                else:
                    result.error += f" (回滚失败: {rollback_result['message']})"

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
) -> OptimizationResult:
    """运行自动优化（apply_proposal 的别名，保持向后兼容）。

    Args:
        proposal: 优化提案
        root: 项目根目录
        auto_rollback: 测试失败时自动回滚
        dry_run: 仅模拟执行

    Returns:
        优化执行结果
    """
    return await apply_proposal(
        proposal,
        root=root,
        auto_rollback=auto_rollback,
        dry_run=dry_run,
    )


__all__ = ["apply_proposal", "run_auto_optimization"]
