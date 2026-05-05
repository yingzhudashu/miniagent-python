"""Proposal Engine — 优化提案生成器

基于自检报告和提案模板，生成具体的优化提案。

核心功能：
- 匹配痛点与提案模板
- 生成文件变更、测试用例、回滚计划
- 风险等级自动评估（结合学习历史）
- 提案格式化输出

提案模板类型（10种）：
- add-feature: 添加新功能/模块
- improve-architecture: 架构改进
- refactor: 重构代码
- add-test: 添加测试用例
- fix-bug: 修复Bug
- improve-performance: 性能优化
- improve-error-handling: 错误处理改进
- improve-documentation: 文档改进
- add-monitoring: 监控/可观测性
- improve-config: 配置优化
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import (
    FileChange,
    OptimizationProposal,
    InspectionReport,
    PainPoint,
    OptTestCase,
    TestCaseType,
)

# ============================================================================
# 提案模板
# ============================================================================

PROPOSAL_TEMPLATES = {
    "add-feature": {
        "description": "添加新功能/模块",
        "risk_level": "medium",
        "pattern_keywords": ["缺少", "没有", "未实现", "not found", "need"],
        "generate": lambda pp: {
            "type": "add",
            "target": pp.description,
            "description": f"添加新功能: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "提升系统功能和完整性",
        },
    },
    "improve-architecture": {
        "description": "架构改进",
        "risk_level": "high",
        "pattern_keywords": ["架构", "耦合", "依赖", "architecture", "dependency"],
        "generate": lambda pp: {
            "type": "modify",
            "target": pp.description,
            "description": f"架构改进: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "降低耦合度，提升可维护性",
        },
    },
    "refactor": {
        "description": "代码重构",
        "risk_level": "medium",
        "pattern_keywords": ["复杂", "过长", "重复", "complex", "long", "duplication"],
        "generate": lambda pp: {
            "type": "refactor",
            "target": pp.description,
            "description": f"代码重构: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "降低代码复杂度，提升可读性",
        },
    },
    "add-test": {
        "description": "添加测试用例",
        "risk_level": "low",
        "pattern_keywords": ["测试", "test", "覆盖", "coverage"],
        "generate": lambda pp: {
            "type": "add",
            "target": pp.description,
            "description": f"添加测试: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "提高测试覆盖率，减少回归风险",
        },
    },
    "fix-bug": {
        "description": "修复Bug",
        "risk_level": "medium",
        "pattern_keywords": ["错误", "bug", "fail", "crash", "exception"],
        "generate": lambda pp: {
            "type": "modify",
            "target": pp.description,
            "description": f"修复Bug: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "修复已知问题，提升稳定性",
        },
    },
    "improve-performance": {
        "description": "性能优化",
        "risk_level": "medium",
        "pattern_keywords": ["性能", "慢", "timeout", "performance", "slow"],
        "generate": lambda pp: {
            "type": "modify",
            "target": pp.description,
            "description": f"性能优化: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "提升运行效率，降低资源消耗",
        },
    },
    "improve-error-handling": {
        "description": "错误处理改进",
        "risk_level": "low",
        "pattern_keywords": ["异常", "except", "error", "handle", "try"],
        "generate": lambda pp: {
            "type": "modify",
            "target": pp.description,
            "description": f"错误处理改进: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "提升错误处理质量，减少崩溃",
        },
    },
    "improve-documentation": {
        "description": "文档改进",
        "risk_level": "low",
        "pattern_keywords": ["文档", "doc", "注释", "comment", "readme"],
        "generate": lambda pp: {
            "type": "modify",
            "target": pp.description,
            "description": f"文档改进: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "提升文档质量，降低理解成本",
        },
    },
    "add-monitoring": {
        "description": "监控/可观测性",
        "risk_level": "low",
        "pattern_keywords": ["监控", "log", "monitor", "trace", "metric"],
        "generate": lambda pp: {
            "type": "add",
            "target": pp.description,
            "description": f"添加监控: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "提升可观测性，快速定位问题",
        },
    },
    "improve-config": {
        "description": "配置优化",
        "risk_level": "medium",
        "pattern_keywords": ["配置", "config", "env", "环境变量"],
        "generate": lambda pp: {
            "type": "modify",
            "target": pp.description,
            "description": f"配置优化: {pp.description[:80]}",
            "rationale": f"根据自检报告: {pp.description}",
            "expected_benefit": "优化配置管理，提升灵活性",
        },
    },
}


def _match_template(pain_point: PainPoint) -> str | None:
    """根据痛点关键词匹配提案模板。"""
    desc = pain_point.description.lower()
    evidence = pain_point.evidence.lower()
    text = f"{desc} {evidence}"

    best_match = None
    best_score = 0
    for template_id, template in PROPOSAL_TEMPLATES.items():
        score = 0
        for keyword in template["pattern_keywords"]:
            if keyword.lower() in text:
                score += 1
        if score > best_score:
            best_score = score
            best_match = template_id

    return best_match


def _generate_proposal_id(template_id: str, target: str) -> str:
    """生成提案ID。"""
    hash_input = f"{template_id}:{target}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:6]
    return f"opt-{template_id}-{short_hash}"


def _generate_file_changes(proposal: dict[str, Any], inspection: InspectionReport) -> list[FileChange]:
    """根据提案生成文件变更。"""
    changes: list[FileChange] = []
    proposal_type = proposal.get("type", "add")

    # 从痛点中提取可能的文件路径
    target = proposal.get("target", "")

    # 查找相关模块
    related_modules = []
    for module in inspection.module_analysis:
        if any(kw in module.path.lower() for kw in target.lower().split()):
            related_modules.append(module)

    if proposal_type == "add":
        # 添加新文件
        if related_modules:
            base_dir = os.path.dirname(related_modules[0].path)
            new_file = f"{base_dir}/new_feature.py" if base_dir else "src/core/new_feature.py"
        else:
            new_file = "src/core/new_feature.py"
        changes.append(FileChange(
            path=new_file,
            action="create",
            description=f"创建新模块: {target[:50]}",
        ))
    elif proposal_type == "modify":
        # 修改现有文件
        for module in related_modules[:2]:
            changes.append(FileChange(
                path=module.path,
                action="modify",
                description=f"修改: {module.path}",
            ))
        if not related_modules:
            changes.append(FileChange(
                path="src/core/unknown.py",
                action="modify",
                description=f"修改相关文件以支持: {target[:50]}",
            ))
    elif proposal_type == "refactor":
        # 重构文件
        for module in related_modules[:3]:
            changes.append(FileChange(
                path=module.path,
                action="modify",
                description=f"重构: {module.path}",
            ))
    elif proposal_type == "remove":
        changes.append(FileChange(
            path=target,
            action="delete",
            description=f"删除: {target}",
        ))

    return changes


def _generate_test_cases(proposal: dict[str, Any], proposal_type: str) -> list[OptTestCase]:
    """为提案生成测试用例。"""
    test_cases: list[OptTestCase] = []
    proposal_id = proposal.get("id", "unknown")
    target = proposal.get("target", "unknown")

    test_cases.append(OptTestCase(
        id=f"test-{proposal_id}-import",
        type="unit",
        description=f"验证 {target[:40]} 可以正确导入",
        setup="import the new module",
        action=f"import {proposal_id}",
        expected="import succeeds without error",
        command=f"python -c 'from {proposal_id} import *'",
    ))

    if proposal_type == "add":
        test_cases.append(OptTestCase(
            id=f"test-{proposal_id}-basic",
            type="unit",
            description=f"验证 {target[:40]} 基本功能",
            setup="initialize the new module",
            action="call main function",
            expected="returns expected result",
            command=f"python -m pytest tests/test_{proposal_id}.py -v",
        ))
    elif proposal_type == "modify":
        test_cases.append(OptTestCase(
            id=f"test-{proposal_id}-regression",
            type="unit",
            description=f"验证修改不破坏现有功能",
            setup="run existing test suite",
            action="python -m pytest",
            expected="all tests pass",
            command="python -m pytest tests/ -v",
        ))

    return test_cases


async def generate_proposals(
    inspection: InspectionReport,
    learning_insights: dict[str, Any] | None = None,
    max_proposals: int = 10,
) -> list[OptimizationProposal]:
    """基于自检报告生成优化提案。

    Args:
        inspection: 自检报告。
        learning_insights: 学习历史（可选，用于风险调整）。
        max_proposals: 最大提案数量。

    Returns:
        优化提案列表。
    """
    proposals: list[OptimizationProposal] = []
    learning = learning_insights or {}
    template_stats = learning.get("templateStats", {})

    for pain_point in inspection.pain_points:
        if len(proposals) >= max_proposals:
            break

        template_id = _match_template(pain_point)
        if template_id is None:
            continue

        template = PROPOSAL_TEMPLATES[template_id]
        proposal_data = template["generate"](pain_point)
        proposal_id = _generate_proposal_id(template_id, proposal_data["target"])

        # 调整风险等级
        risk_level = template["risk_level"]
        template_success = template_stats.get(template_id, {}).get("successRate", None)
        if template_success is not None:
            if template_success < 0.3:
                # 历史成功率低，提升风险
                if risk_level == "low":
                    risk_level = "medium"
                elif risk_level == "medium":
                    risk_level = "high"
            elif template_success > 0.8:
                # 历史成功率高，降低风险
                if risk_level == "high":
                    risk_level = "medium"
                elif risk_level == "medium":
                    risk_level = "low"

        # 从痛点中提取文件路径（如果有）
        file_path_match = re.search(r"([a-zA-Z0-9_/]+\.[a-z]+)", pain_point.description)
        if file_path_match:
            proposal_data["file_path"] = file_path_match.group(1)

        # 生成文件变更
        file_changes = _generate_file_changes(proposal_data, inspection)

        # 生成测试用例
        test_cases = _generate_test_cases(proposal_data, proposal_data["type"])

        proposal = OptimizationProposal(
            id=proposal_id,
            type=proposal_data["type"],
            risk_level=risk_level,
            target=proposal_data["target"],
            description=proposal_data["description"],
            rationale=proposal_data["rationale"],
            expected_benefit=proposal_data["expected_benefit"],
            files=file_changes,
            dependencies=[],
            test_cases=test_cases,
            rollback_plan=f"Revert changes from {proposal_id}",
            estimated_time_seconds=60 if risk_level == "low" else 120 if risk_level == "medium" else 300,
        )
        proposals.append(proposal)

    return proposals


def format_proposals(proposals: list[OptimizationProposal]) -> str:
    """格式化提案为可读字符串。"""
    if not proposals:
        return "No proposals generated."

    lines = ["# Optimization Proposals\n"]
    for i, p in enumerate(proposals, 1):
        lines.append(f"## {i}. [{p.risk_level.upper()}] {p.description}")
        lines.append(f"- **ID**: {p.id}")
        lines.append(f"- **Type**: {p.type}")
        lines.append(f"- **Target**: {p.target}")
        lines.append(f"- **Rationale**: {p.rationale}")
        lines.append(f"- **Expected Benefit**: {p.expected_benefit}")
        lines.append(f"- **Files**: {len(p.files)} changes")
        lines.append(f"- **Test Cases**: {len(p.test_cases)}")
        if p.rollback_plan:
            lines.append(f"- **Rollback**: {p.rollback_plan}")
        lines.append("")

    return "\n".join(lines)
