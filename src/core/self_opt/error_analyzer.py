"""错误分析引擎 (Phase 5.2 新增)

读取运行时错误日志，聚类相同类型错误，按频率排序，
生成可对接优化提案的错误分析报告。

工作流程：
1. 读取 errors/error-log.jsonl 日志文件
2. 按 stack_hash 聚类相同错误，统计频率
3. 按频率排序，优先分析高频错误
4. 生成 ErrorAnalysis 报告，可对接 generate_proposals()

设计原则：
- 频率优先：出现越多次的错误，优先级越高
- 自动分类：将错误归类为 crash / type-error / runtime / performance / logic
- 可行动性：每个错误簇都附带修复建议
- 与 Inspector 对接：运行时错误 > 静态分析
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .types import InspectionReport, PainPoint
from .runtime_error_collector import RuntimeErrorRecord, parse_error_log

# 错误分类
ErrorCategory = str  # "crash" | "type-error" | "runtime" | "performance" | "logic" | "unknown"


@dataclass
class ErrorCluster:
    """错误簇：相同 stack_hash 的错误集合。"""
    stack_hash: str
    error_type: str
    latest_message: str
    latest_stack: str
    count: int
    first_seen: str
    last_seen: str
    tools: list[str] = field(default_factory=list)
    contexts: list[dict] = field(default_factory=list)
    category: ErrorCategory = "unknown"
    suggestion: str = ""
    severity: int = 2


@dataclass
class ErrorAnalysis:
    """错误分析结果。"""
    timestamp: str
    total_errors: int
    cluster_count: int
    clusters: list[ErrorCluster] = field(default_factory=list)
    category_stats: dict[str, int] = field(default_factory=dict)
    frequent_errors: list[ErrorCluster] = field(default_factory=list)
    pain_points: list[PainPoint] = field(default_factory=list)
    summary: str = ""


def _classify_error(error_type: str, message: str) -> ErrorCategory:
    """根据错误类型和消息判断错误分类。"""
    t = error_type.lower()
    msg = message.lower()

    if "typeerror" in t or "undefined" in msg or "null" in msg or "not a function" in msg:
        return "type-error"
    if "timeout" in t or "timeout" in msg or "memory" in msg or "heap" in msg:
        return "performance"
    if "error" in t and ("crash" in msg or "fatal" in msg or "uncaught" in msg):
        return "crash"
    if any(x in t for x in ["rangeerror", "syntaxerror", "referenceerror"]):
        return "runtime"
    if any(x in msg for x in ["not found", "no such", "eperm", "eaccess", "file not found", "no such file"]):
        return "runtime"
    if any(x in msg for x in ["expected", "invalid", "assertion"]):
        return "logic"
    return "unknown"


def _generate_suggestion(cluster: ErrorCluster) -> str:
    """根据错误分类生成修复建议。"""
    suggestions = {
        "crash": "程序崩溃，需要添加 try-except 保护或修复根本原因",
        "type-error": "类型错误，检查变量是否为 None，添加类型守卫",
        "runtime": "运行时错误，检查文件路径、权限、网络连接等外部依赖",
        "performance": "性能问题，考虑增加超时时间、优化算法或增加内存限制",
        "logic": "逻辑错误，检查条件判断和边界情况",
        "unknown": "未知错误，需要人工分析堆栈追踪",
    }
    return suggestions.get(cluster.category, "需要人工分析")


def _calculate_severity(category: ErrorCategory, count: int) -> int:
    """根据错误分类和频率计算严重程度 (1-5)。"""
    base = 2
    if category == "crash":
        base = 4
    elif category in ("type-error", "performance"):
        base = 3

    if count >= 20:
        base += 1
    if count >= 50:
        base += 1

    return min(base, 5)


def _get_error_log_path(errors_dir: str | None = None) -> str:
    """获取错误日志路径。"""
    import datetime
    dir_path = errors_dir or os.path.join(os.getcwd(), "errors")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    return os.path.join(dir_path, f"error-log-{today}.jsonl")


async def analyze_errors(
    errors_dir: str | None = None,
) -> ErrorAnalysis:
    """分析运行时错误日志。

    读取错误日志，聚类相同错误，生成分析报告。
    报告可直接对接 Inspector 和 Proposal Engine。

    Args:
        errors_dir: 错误日志目录（默认：当前目录/errors）。

    Returns:
        错误分析报告。
    """
    import datetime

    log_path = _get_error_log_path(errors_dir)
    records = parse_error_log(log_path=log_path, limit=1000)

    # 按 stack_hash 聚类
    cluster_map: dict[str, list[RuntimeErrorRecord]] = {}
    for r in records:
        cluster_map.setdefault(r.stack_hash, []).append(r)

    # 构建错误簇
    clusters: list[ErrorCluster] = []
    for hash_val, recs in cluster_map.items():
        sorted_recs = sorted(recs, key=lambda r: r.timestamp)
        first = sorted_recs[0]
        last = sorted_recs[-1]

        category = _classify_error(first.error_type, first.message)
        tools = list({r.context.tool for r in recs if r.context.tool})

        cluster = ErrorCluster(
            stack_hash=hash_val,
            error_type=first.error_type,
            latest_message=last.message,
            latest_stack=last.stack,
            count=len(recs),
            first_seen=first.timestamp,
            last_seen=last.timestamp,
            tools=tools,
            contexts=[{
                "tool": r.context.tool,
                "input": r.context.input,
                "proposal_id": r.context.proposal_id,
            } for r in recs],
            category=category,
        )
        cluster.suggestion = _generate_suggestion(cluster)
        cluster.severity = _calculate_severity(category, len(recs))
        clusters.append(cluster)

    # 按频率降序
    clusters.sort(key=lambda c: c.count, reverse=True)

    # 分类统计
    category_stats: dict[str, int] = {}
    for c in clusters:
        category_stats[c.category] = category_stats.get(c.category, 0) + c.count

    # 高频错误（>= 5 次）
    frequent_errors = [c for c in clusters if c.count >= 5]

    # 转化为痛点
    pain_points: list[PainPoint] = []
    for c in clusters:
        tool_info = f" (工具: {', '.join(c.tools)})" if c.tools else ""
        severity_map = {5: "high", 4: "high", 3: "medium", 2: "low", 1: "low"}
        pain_points.append(PainPoint(
            description=f"[运行时] {c.error_type}: {c.latest_message[:100]}{tool_info} — 出现 {c.count} 次",
            severity=severity_map.get(c.severity, "low"),
            evidence=f"运行时错误日志: {c.stack_hash} ({c.count} 次)",
        ))

    # 摘要
    freq_summary = ", ".join(
        f"{c.error_type} x{c.count}" for c in frequent_errors[:3]
    ) if frequent_errors else "无"
    summary = (
        f"共 {len(records)} 个错误，{len(clusters)} 种类型。"
        f"高频错误 ({len(frequent_errors)} 种): {freq_summary}。"
        f"建议优先修复: {clusters[0].error_type} ({clusters[0].count} 次)"
        if clusters else "无错误"
    )

    return ErrorAnalysis(
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        total_errors=len(records),
        cluster_count=len(clusters),
        clusters=clusters,
        category_stats=category_stats,
        frequent_errors=frequent_errors,
        pain_points=pain_points,
        summary=summary,
    )


def inject_errors_into_inspection(
    report: InspectionReport,
    error_analysis: ErrorAnalysis,
) -> InspectionReport:
    """将错误分析报告注入到自检报告中。

    Phase 5.2.3: Inspector 接入运行时数据
    运行时错误优先级 > 静态分析

    Args:
        report: 已有的自检报告（会被直接修改）。
        error_analysis: 错误分析报告。

    Returns:
        注入后的报告。
    """
    # 运行时错误插入到 pain_points 最前面（优先级最高）
    report.pain_points = list(error_analysis.pain_points) + report.pain_points

    # 更新建议
    if error_analysis.frequent_errors:
        top_error = error_analysis.frequent_errors[0]
        report.suggestions.insert(
            0,
            f"修复高频运行时错误: {top_error.error_type} "
            f"(出现 {top_error.count} 次) — {top_error.suggestion}"
        )

    # 更新摘要
    report.summary = f"[运行时] {error_analysis.summary}。[静态] {report.summary}"

    return report
