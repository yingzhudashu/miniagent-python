"""Self-optimization subsystem — 提案引擎

基于检查报告（InspectionReport）生成优化提案。

功能：
- 分析痛点并生成对应提案
- 评估提案风险等级
- 生成文件变更计划
- 按优先级排序提案

详见 ``docs/SELF_OPT.md``。
"""

from __future__ import annotations

import re
import uuid

from miniagent.core.self_opt.types import (
    FileChange,
    InspectionReport,
    OptimizationProposal,
    OptTestCase,
    PainPoint,
)
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def _generate_proposal_id() -> str:
    """生成唯一提案 ID。"""
    return f"opt-{uuid.uuid4().hex[:8]}"


def _pain_point_to_proposal(pain: PainPoint, root: str = "") -> OptimizationProposal | None:
    """将痛点转换为优化提案。

    Args:
        pain: 痛点信息
        root: 项目根目录

    Returns:
        优化提案（如果可生成）
    """
    if pain.severity <= 2:
        risk_level = "low"
    elif pain.severity <= 3:
        risk_level = "medium"
    else:
        risk_level = "high"

    proposal = OptimizationProposal(
        id=_generate_proposal_id(),
        type="refactor",
        risk_level=risk_level,
        target=pain.description,
        description=f"解决痛点: {pain.description}",
        rationale=pain.suggestion,
        expected_benefit=f"降低严重程度（当前 {pain.severity}/5）",
        estimated_effort=pain.severity * 15,  # 严重程度越高，预估工时越长
    )

    # 根据痛点类型添加文件变更
    if "缺少 __init__.py" in pain.description:
        proposal.type = "add"
        match = re.search(r"目录\s+(\S+)\s+包含", pain.description)
        if match:
            rel_dir = match.group(1).replace("\\", "/")
            init_path = f"{rel_dir}/__init__.py"
            proposal.files.append(
                FileChange(
                    path=init_path,
                    action="create",
                    content='"""包模块"""\n',
                    reason="添加包标识文件",
                )
            )
    elif "文件过大" in pain.description:
        proposal.type = "refactor"
        proposal.risk_level = "medium"
    elif "缺少文档" in pain.description:
        proposal.type = "add"
        proposal.files.append(
            FileChange(
                path="CHANGELOG.md",
                action="create",
                content="# 变更日志\n\n## [Unreleased]\n",
                reason="添加变更日志",
            )
        )

    return proposal


def _generate_test_proposals(report: InspectionReport) -> list[OptimizationProposal]:
    """基于测试覆盖率生成提案。

    Args:
        report: 检查报告

    Returns:
        测试相关提案
    """
    proposals = []

    if report.test_coverage < 50:
        proposals.append(
            OptimizationProposal(
                id=_generate_proposal_id(),
                type="add",
                risk_level="low",
                target="tests/",
                description="提高测试覆盖率",
                rationale=f"当前测试覆盖率约 {report.test_coverage}%，建议提升至 80%+",
                expected_benefit="提高代码可靠性，减少回归 bug",
                estimated_effort=120,
                test_cases=[
                    OptTestCase(
                        id="tc-coverage",
                        type="unit",
                        description="验证测试覆盖率提升",
                        action="运行 coverage report",
                        expected="覆盖率 > 80%",
                        command="coverage report --show-missing",
                    ),
                ],
            )
        )

    return proposals


async def generate_proposals(
    report: InspectionReport,
    *,
    root: str = "",
    max_proposals: int = 10,
    min_severity: int = 1,
) -> list[OptimizationProposal]:
    """基于检查报告生成优化提案。

    Args:
        report: 项目检查报告
        root: 项目根目录
        max_proposals: 最大提案数量
        min_severity: 最低痛点严重程度（1-5）

    Returns:
        优化提案列表（按优先级排序）
    """
    proposals: list[OptimizationProposal] = []

    # 从痛点生成提案
    for pain in report.pain_points:
        if pain.severity >= min_severity:
            proposal = _pain_point_to_proposal(pain, root)
            if proposal:
                proposals.append(proposal)

    # 测试覆盖率提案
    test_proposals = _generate_test_proposals(report)
    proposals.extend(test_proposals)

    # 从模块问题生成提案
    for module in report.modules:
        if len(module.issues) > 2:
            proposals.append(
                OptimizationProposal(
                    id=_generate_proposal_id(),
                    type="refactor",
                    risk_level="medium",
                    target=module.path,
                    description=f"重构模块: {module.path}",
                    rationale=f"发现 {len(module.issues)} 个问题: {'; '.join(module.issues)}",
                    expected_benefit="提高代码质量和可维护性",
                    estimated_effort=module.lines // 10,  # 每 10 行约 1 分钟
                )
            )

    # 按风险等级和预估工时排序（低风险、短工时优先）
    risk_order = {"low": 0, "medium": 1, "high": 2}
    proposals.sort(key=lambda p: (risk_order.get(p.risk_level, 1), p.estimated_effort))

    # 限制数量
    result = proposals[:max_proposals]

    _logger.info("生成 %d 个优化提案", len(result))
    return result


__all__ = ["generate_proposals"]
