"""Optimization Learner — 优化学习器

从优化历史中学习，调整风险策略。

功能：
- 加载优化历史（来自 structured_logger 或本地文件）
- 分析成功率、失败模式
- 动态调整提案模板风险等级
- 禁用持续失败的模板

设计原则：
- 历史数据驱动：所有调整基于实际执行结果
- 时间衰减：近期数据权重更高
- 模板级学习：针对每个提案模板独立学习
- 安全优先：失败模板自动禁用
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import OptimizationResult, RiskLevel
from .structured_logger import load_optimization_log, read_raw_structured_log

# 默认配置
DEFAULT_LEARNER_DIR = ".self-opt"
DEFAULT_HISTORY_FILE = "optimization_history.json"

# 连续失败阈值（超过此值自动禁用模板）
DISABLE_THRESHOLD = 5


@dataclass
class TemplateStats:
    """单个提案模板的统计数据。"""
    total: int = 0
    success: int = 0
    failed: int = 0
    reverted: int = 0
    avg_duration_seconds: float = 0.0
    last_failure_reason: str = ""
    consecutive_failures: int = 0
    disabled: bool = False
    risk_adjustment: float = 0.0  # 风险调整值 (-1 到 1)


@dataclass
class LearningInsights:
    """学习洞察。"""
    total_optimizations: int = 0
    overall_success_rate: float = 0.0
    template_stats: dict[str, TemplateStats] = field(default_factory=dict)
    disabled_templates: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


def _get_history_path(project_root: str, learner_dir: str = DEFAULT_LEARNER_DIR) -> str:
    """获取历史文件路径。"""
    path = os.path.join(project_root, learner_dir)
    Path(path).mkdir(parents=True, exist_ok=True)
    return os.path.join(path, DEFAULT_HISTORY_FILE)


async def load_history(
    project_root: str,
    learner_dir: str = DEFAULT_LEARNER_DIR,
) -> list[OptimizationResult]:
    """加载优化历史。

    优先从 structured_logger 的 JSONL 日志读取，
    回退到本地 history 文件。

    Args:
        project_root: 项目根目录。
        learner_dir: 学习数据目录。

    Returns:
        优化结果列表。
    """
    history: list[OptimizationResult] = []

    # 方式 1: 从 structured_logger 读取
    try:
        log_entries = load_optimization_log(project_root)
        for entry in log_entries:
            history.append(entry.result)
    except Exception:
        pass

    # 方式 2: 从本地 history 文件读取
    if not history:
        history_path = _get_history_path(project_root, learner_dir)
        if os.path.exists(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    history.append(OptimizationResult(
                        proposal_id=item.get("proposalId", ""),
                        status=item.get("status", "failed"),
                        test_results=item.get("testResults", []),
                        fix_attempts=item.get("fixAttempts", 0),
                        reverted=item.get("reverted", False),
                        lesson=item.get("lesson", ""),
                        timestamp=item.get("timestamp", ""),
                        total_duration_seconds=item.get("totalDurationSeconds", 0.0),
                    ))
            except Exception:
                pass

    return history


async def analyze_history(
    history: list[OptimizationResult],
) -> LearningInsights:
    """分析优化历史，生成学习洞察。

    Args:
        history: 优化结果列表。

    Returns:
        学习洞察。
    """
    if not history:
        return LearningInsights(
            total_optimizations=0,
            overall_success_rate=0.0,
        )

    total = len(history)
    success_count = sum(1 for r in history if r.status == "success")
    overall_rate = success_count / max(total, 1)

    # 按提案类型聚合（从 proposal_id 推断）
    template_results: dict[str, list[OptimizationResult]] = {}
    for r in history:
        # 从 proposal_id 提取模板类型，如 "opt-add-feature-abc123" -> "add-feature"
        template_id = _extract_template_id(r.proposal_id)
        template_results.setdefault(template_id, []).append(r)

    template_stats: dict[str, TemplateStats] = {}
    for template_id, results in template_results.items():
        stats = TemplateStats()
        stats.total = len(results)
        stats.success = sum(1 for r in results if r.status == "success")
        stats.failed = sum(1 for r in results if r.status == "failed")
        stats.reverted = sum(1 for r in results if r.reverted)

        durations = [r.total_duration_seconds for r in results if r.total_duration_seconds > 0]
        if durations:
            stats.avg_duration_seconds = sum(durations) / len(durations)

        # 找到最近的失败原因
        for r in reversed(results):
            if r.status == "failed" and r.lesson:
                stats.last_failure_reason = r.lesson
                break

        # 计算连续失败次数（从最新开始）
        consecutive = 0
        for r in reversed(results):
            if r.status == "success":
                break
            consecutive += 1
        stats.consecutive_failures = consecutive

        # 检查是否需要禁用
        if stats.consecutive_failures >= DISABLE_THRESHOLD:
            stats.disabled = True

        # 计算风险调整值
        if stats.total >= 3:
            success_rate = stats.success / stats.total
            if success_rate < 0.3:
                stats.risk_adjustment = 0.5  # 提高风险
            elif success_rate > 0.8:
                stats.risk_adjustment = -0.3  # 降低风险

        template_stats[template_id] = stats

    # 生成建议
    suggestions: list[str] = []
    for tid, stats in template_stats.items():
        if stats.disabled:
            suggestions.append(f"禁用模板 {tid}（连续失败 {stats.consecutive_failures} 次）")
        elif stats.risk_adjustment > 0:
            suggestions.append(f"模板 {tid} 成功率低 ({stats.success}/{stats.total})，建议提升风险等级")
        elif stats.risk_adjustment < 0:
            suggestions.append(f"模板 {tid} 成功率高 ({stats.success}/{stats.total})，可尝试更大改动")

    return LearningInsights(
        total_optimizations=total,
        overall_success_rate=round(overall_rate, 3),
        template_stats=template_stats,
        disabled_templates=[tid for tid, s in template_stats.items() if s.disabled],
        suggestions=suggestions,
    )


def _extract_template_id(proposal_id: str) -> str:
    """从提案ID提取模板类型。"""
    # 格式: "opt-add-feature-abc123" -> "add-feature"
    parts = proposal_id.split("-")
    if len(parts) >= 3:
        return "-".join(parts[1:-1])  # 去掉 "opt" 和最后的 hash
    return "unknown"


async def adjust_risk(
    proposal_id: str,
    current_risk: RiskLevel,
    insights: LearningInsights,
) -> RiskLevel:
    """根据学习洞察调整提案风险等级。

    Args:
        proposal_id: 提案ID。
        current_risk: 当前风险等级。
        insights: 学习洞察。

    Returns:
        调整后的风险等级。
    """
    template_id = _extract_template_id(proposal_id)
    stats = insights.template_stats.get(template_id)

    if stats is None:
        return current_risk

    risk_order = ["low", "medium", "high", "destructive"]
    current_index = risk_order.index(current_risk) if current_risk in risk_order else 1

    # 根据风险调整值移动
    adjustment = stats.risk_adjustment
    if adjustment > 0:
        new_index = min(current_index + 1, len(risk_order) - 1)
    elif adjustment < 0:
        new_index = max(current_index - 1, 0)
    else:
        new_index = current_index

    return risk_order[new_index]


async def get_disabled_templates(
    insights: LearningInsights | None = None,
    project_root: str | None = None,
) -> list[str]:
    """获取已禁用的模板列表。

    Args:
        insights: 已有的学习洞察（如果提供，直接使用）。
        project_root: 项目根目录（用于加载历史）。

    Returns:
        已禁用模板ID列表。
    """
    if insights:
        return insights.disabled_templates

    if project_root:
        history = await load_history(project_root)
        insights = await analyze_history(history)
        return insights.disabled_templates

    return []


async def save_learning_state(
    project_root: str,
    insights: LearningInsights,
    learner_dir: str = DEFAULT_LEARNER_DIR,
) -> None:
    """保存学习状态到本地文件。

    Args:
        project_root: 项目根目录。
        insights: 学习洞察。
        learner_dir: 学习数据目录。
    """
    history_path = _get_history_path(project_root, learner_dir)

    data = {
        "totalOptimizations": insights.total_optimizations,
        "overallSuccessRate": insights.overall_success_rate,
        "templateStats": {
            tid: {
                "total": s.total,
                "success": s.success,
                "failed": s.failed,
                "reverted": s.reverted,
                "avgDurationSeconds": s.avg_duration_seconds,
                "lastFailureReason": s.last_failure_reason,
                "consecutiveFailures": s.consecutive_failures,
                "disabled": s.disabled,
                "riskAdjustment": s.risk_adjustment,
            }
            for tid, s in insights.template_stats.items()
        },
        "disabledTemplates": insights.disabled_templates,
        "suggestions": insights.suggestions,
    }

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
