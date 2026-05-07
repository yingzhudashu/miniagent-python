"""Auto Optimizer — 自动优化编排器

编排完整的自动优化流程：

    inspect → research → propose → snapshot → execute → test → fix/rollback → log

核心规则：
- 仅自动执行低风险提案
- 中高风险提案需要确认
- 失败自动修复（最多 2 次）
- 修复失败自动回滚

设计原则：
- 安全第一：Git 快照保护
- 渐进式优化：从低风险开始
- 自动修复：最多 2 次修复尝试
- 自动回滚：修复失败时回滚
- 完整日志：所有操作记录在案
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .types import (
    InspectionReport,
    OptimizationProposal,
    OptimizationResult,
    ResearchReport,
    RiskLevel,
)
from .inspector import inspect_project
from .proposal_engine import generate_proposals, format_proposals
from .self_test_runner import execute_optimization, apply_file_changes
from .git_snapshot import (
    create_snapshot,
    revert_to_snapshot,
    finalize_snapshot,
    is_in_git_repo,
    SnapshotInfo,
)
from .structured_logger import (
    log_optimize_start,
    log_optimize_complete,
    log_proposal_executed,
)
from .diff_generator import generate_fix_diff, apply_diff
from .confirmation_manager import ConfirmationManager
from .optimization_learner import load_history, analyze_history, LearningInsights
from .researcher import research_topic


@dataclass
class AutoOptimizeResult:
    """自动优化结果。"""
    inspection: InspectionReport | None = None
    research: ResearchReport | None = None
    proposals: list[OptimizationProposal] = field(default_factory=list)
    results: list[OptimizationResult] = field(default_factory=list)
    executed: int = 0
    succeeded: int = 0
    failed: int = 0
    reverted: int = 0
    skipped: int = 0
    total_duration_seconds: float = 0.0


async def _execute_proposal(
    proposal: OptimizationProposal,
    project_root: str,
    snapshot_info: SnapshotInfo | None = None,
    max_fix_attempts: int = 2,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
) -> OptimizationResult:
    """执行单个优化提案。

    流程：
    1. 应用文件变更
    2. 执行测试
    3. 失败时自动修复
    4. 修复失败时回滚

    Args:
        proposal: 优化提案。
        project_root: 项目根目录。
        snapshot_info: Git 快照信息。
        max_fix_attempts: 最大修复尝试次数。
        model: LLM 模型。
        api_key: API 密钥。

    Returns:
        优化执行结果。
    """
    start_time = time.time()
    result = await execute_optimization(proposal, project_root)
    result.git_snapshot = snapshot_info.branch_name if snapshot_info else None

    # 如果失败且有测试用例，尝试自动修复
    if result.status == "failed" and proposal.test_cases:
        for attempt in range(1, max_fix_attempts + 1):
            # 获取失败测试的信息
            failed_test = None
            for tr in result.test_results:
                if not tr.passed:
                    failed_test = tr
                    break

            if not failed_test:
                break

            print(f"[auto-optimizer] 修复尝试 {attempt}/{max_fix_attempts} for {proposal.id}")

            # 生成修复补丁
            for file_change in proposal.files:
                if file_change.action in ("create", "modify"):
                    fix_result = await generate_and_apply_fix_for_proposal(
                        proposal.id,
                        file_change.path,
                        failed_test.output,
                        project_root,
                        model=model,
                        api_key=api_key,
                    )
                    if fix_result:
                        result.fix_attempts += 1

            # 重新测试
            result = await execute_optimization(proposal, project_root)
            result.git_snapshot = snapshot_info.branch_name if snapshot_info else None
            result.fix_attempts = attempt

            if result.status == "success":
                break

    return result


async def generate_and_apply_fix_for_proposal(
    proposal_id: str,
    file_path: str,
    error_message: str,
    project_root: str,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
) -> bool:
    """为提案生成并应用修复。"""
    success, _ = await generate_fix_diff(
        proposal_id=proposal_id,
        file_path=file_path,
        original_content="",
        error_message=error_message,
        model=model,
        api_key=api_key,
    )
    return success is not None


async def _rollback(
    proposal: OptimizationProposal,
    project_root: str,
    snapshot_info: SnapshotInfo | None = None,
) -> bool:
    """回滚优化提案。

    Args:
        proposal: 优化提案。
        project_root: 项目根目录。
        snapshot_info: Git 快照信息。

    Returns:
        是否回滚成功。
    """
    if snapshot_info:
        success, error = await revert_to_snapshot(project_root, snapshot_info)
        if not success:
            print(f"[auto-optimizer] 回滚失败: {error}")
        return success

    # 无快照时，反向应用文件变更
    for file_change in proposal.files:
        try:
            full_path = os.path.join(project_root, file_change.path)
            if file_change.action == "create" and os.path.exists(full_path):
                os.remove(full_path)
            elif file_change.action == "delete" and file_change.content:
                Path(full_path).write_text(file_change.content, encoding="utf-8")
        except Exception as e:
            print(f"[auto-optimizer] 回滚文件 {file_change.path} 失败: {e}")

    return True


async def run_auto_optimization(
    project_root: str,
    src_dir: str | None = None,
    auto_execute: bool = True,
    max_proposals: int = 10,
    max_fix_attempts: int = 2,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
    confirmation_manager: ConfirmationManager | None = None,
) -> AutoOptimizeResult:
    """运行完整的自动优化流程。

    流程：
    1. 自检 (inspect)
    2. 研究 (research) — 可选
    3. 生成提案 (propose)
    4. 创建快照 (snapshot)
    5. 执行提案 (execute)
    6. 测试 (test)
    7. 修复/回滚 (fix/rollback)
    8. 记录日志 (log)

    Args:
        project_root: 项目根目录。
        src_dir: 源代码目录（默认: project_root/src）。
        auto_execute: 是否自动执行低风险提案。
        max_proposals: 最大提案数。
        max_fix_attempts: 最大修复尝试次数。
        model: LLM 模型。
        api_key: API 密钥。
        confirmation_manager: 确认管理器。

    Returns:
        自动优化结果。
    """
    start_time = time.time()
    src_dir = src_dir or os.path.join(project_root, "src")
    result = AutoOptimizeResult()

    # Step 1: 自检
    print("[auto-optimizer] Step 1: 自检...")
    try:
        inspection = await inspect_project(src_dir)
        result.inspection = inspection
        print(f"  发现 {len(inspection.pain_points)} 个痛点")
    except Exception as e:
        print(f"[auto-optimizer] 自检失败: {e}")
        return result

    # Step 2: 研究（可选）
    print("[auto-optimizer] Step 2: 研究...")
    try:
        pain_points_text = " ".join(pp.description for pp in inspection.pain_points[:3])
        research = await research_topic(pain_points_text or "agent self-optimization")
        result.research = research
        print(f"  找到 {len(research.references)} 个参考")
    except Exception as e:
        print(f"[auto-optimizer] 研究失败: {e}")

    # Step 3: 加载学习历史
    print("[auto-optimizer] Step 3: 加载学习历史...")
    learning_insights: LearningInsights | None = None
    try:
        history = await load_history(project_root)
        if history:
            learning_insights = await analyze_history(history)
            print(f"  历史优化数: {learning_insights.total_optimizations}")
    except Exception as e:
        print(f"[auto-optimizer] 加载历史失败: {e}")

    # Step 4: 生成提案
    print("[auto-optimizer] Step 4: 生成提案...")
    try:
        proposals = await generate_proposals(
            inspection,
            learning_insights=learning_insights.__dict__ if learning_insights else None,
            max_proposals=max_proposals,
        )
        result.proposals = proposals
        print(f"  生成 {len(proposals)} 个提案")
    except Exception as e:
        print(f"[auto-optimizer] 生成提案失败: {e}")
        return result

    # Step 5: 记录开始
    log_optimize_start(project_root, len(proposals))

    # 如果没有提案，直接结束
    if not proposals:
        log_optimize_complete(project_root, 0, 0, 0, 0)
        return result

    # Step 6: 创建 Git 快照
    print("[auto-optimizer] Step 6: 创建 Git 快照...")
    snapshot_info: SnapshotInfo | None = None
    in_git = await is_in_git_repo(project_root)
    if in_git:
        snapshot_info = await create_snapshot(project_root, msg="Pre-optimization snapshot")
        if snapshot_info:
            print(f"  快照分支: {snapshot_info.branch_name}")
        else:
            print("  快照创建失败，继续执行但无回滚保护")
    else:
        print("  不在 Git 仓库中，跳过快照")

    # Step 7: 执行提案
    print("[auto-optimizer] Step 7: 执行提案...")
    for i, proposal in enumerate(proposals):
        # 过滤已禁用的模板
        if learning_insights and proposal.id:
            from .optimization_learner import _extract_template_id
            template_id = _extract_template_id(proposal.id)
            if template_id in learning_insights.disabled_templates:
                print(f"  [{i+1}/{len(proposals)}] 跳过 {proposal.id}（模板已禁用）")
                result.skipped += 1
                continue

        # 风险确认
        if not auto_execute and proposal.risk_level != "low":
            if confirmation_manager:
                confirmed = await confirmation_manager.request_confirmation(proposal)
                if not confirmed:
                    print(f"  [{i+1}/{len(proposals)}] 跳过 {proposal.id}（用户拒绝）")
                    result.skipped += 1
                    continue
            else:
                print(f"  [{i+1}/{len(proposals)}] 跳过 {proposal.id}（{proposal.risk_level} 风险，auto_execute=False）")
                result.skipped += 1
                continue

        print(f"  [{i+1}/{len(proposals)}] 执行 {proposal.id} [{proposal.risk_level}] {proposal.description[:50]}")

        # 执行提案
        exec_result = await _execute_proposal(
            proposal,
            project_root,
            snapshot_info=snapshot_info,
            max_fix_attempts=max_fix_attempts,
            model=model,
            api_key=api_key,
        )
        result.results.append(exec_result)
        result.executed += 1

        # 记录结果
        log_proposal_executed(project_root, exec_result)

        if exec_result.status == "success":
            result.succeeded += 1
            print(f"    ✅ 成功 ({exec_result.total_duration_seconds:.1f}s)")

            # 最终化快照
            if snapshot_info:
                await finalize_snapshot(
                    project_root,
                    snapshot_info,
                    commit_msg=f"optimization: {proposal.description[:80]}",
                )

        else:
            result.failed += 1
            print(f"    ❌ 失败: {exec_result.lesson}")

            # 回滚
            if snapshot_info:
                print(f"    🔄 回滚中...")
                rollback_success = await _rollback(proposal, project_root, snapshot_info)
                if rollback_success:
                    result.reverted += 1
                    exec_result.reverted = True
                    print(f"    ✅ 回滚成功")
                else:
                    print(f"    ⚠️ 回滚失败")

    # Step 8: 记录完成
    elapsed = time.time() - start_time
    result.total_duration_seconds = elapsed
    log_optimize_complete(
        project_root,
        result.executed,
        result.succeeded,
        result.failed,
        result.reverted,
    )

    print(f"\n[auto-optimizer] 优化完成: {result.succeeded}/{result.executed} 成功, "
          f"{result.failed} 失败, {result.reverted} 回滚 ({elapsed:.1f}s)")

    return result
