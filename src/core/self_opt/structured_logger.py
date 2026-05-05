"""结构化 JSON 日志组件 (Phase 5.5 新增)

Self-Optimization 子系统的结构化日志组件。

替代旧的 OPTIMIZATION_LOG.md 混合格式，使用纯 JSON Lines 格式：
- 每行一个完整 JSON 对象
- 便于程序解析、可视化、历史对比
- 支持日志轮转（超过 10MB 自动归档）
- 向后兼容：可读取旧格式

日志事件类型：
- optimize_start: 优化流程开始
- optimize_complete: 优化流程完成
- proposal_generated: 提案生成
- proposal_executed: 提案执行
- test_run: 测试执行
- fix_attempted: 修复尝试
- rollback: 回滚
- error: 运行时错误

文件结构：
- 主日志: logs/optimization.jsonl
- 归档日志: logs/optimization.YYYY-MM-DD.jsonl（轮转时生成）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .types import OptimizationResult, OptimizationLogEntry, OptimizationType, RiskLevel

# 日志事件类型
StructuredLogEventType = str

# 默认配置
DEFAULT_LOG_DIR = "logs"
DEFAULT_LOG_FILENAME = "optimization.jsonl"
DEFAULT_MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB


@dataclass
class StructuredLogEntry:
    """结构化日志条目。"""

    type: str
    timestamp: str
    payload: dict[str, Any]
    version: str | None = None
    proposal_id: str | None = None
    test_case_id: str | None = None


def _ensure_log_dir(project_root: str, log_dir: str) -> str:
    """确保日志目录存在。"""
    full_path = log_dir if os.path.isabs(log_dir) else os.path.join(project_root, log_dir)
    Path(full_path).mkdir(parents=True, exist_ok=True)
    return full_path


def _get_log_path(project_root: str, log_dir: str = DEFAULT_LOG_DIR, log_filename: str = DEFAULT_LOG_FILENAME) -> str:
    """获取日志文件路径。"""
    log_dir = _ensure_log_dir(project_root, log_dir)
    return os.path.join(log_dir, log_filename)


def _rotate_log_if_needed(log_path: str, max_log_size: int = DEFAULT_MAX_LOG_SIZE) -> None:
    """日志轮转：超过最大大小时，归档当前日志。"""
    if not os.path.exists(log_path):
        return

    stats = os.stat(log_path)
    if stats.st_size < max_log_size:
        return

    # 归档为带日期的文件名
    import datetime

    dir_path = os.path.dirname(log_path)
    base = os.path.splitext(os.path.basename(log_path))[0]
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    archive_path = os.path.join(dir_path, f"{base}.{date}.jsonl")

    # 如果归档文件已存在，加序号
    final_path = archive_path
    counter = 1
    while os.path.exists(final_path):
        final_path = os.path.join(dir_path, f"{base}.{date}.{counter}.jsonl")
        counter += 1

    os.rename(log_path, final_path)


def _append_structured_log(
    project_root: str,
    entry: StructuredLogEntry,
    log_dir: str = DEFAULT_LOG_DIR,
    max_log_size: int = DEFAULT_MAX_LOG_SIZE,
) -> None:
    """追加一条结构化日志。"""
    log_path = _get_log_path(project_root, log_dir)
    _rotate_log_if_needed(log_path, max_log_size)

    line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


def log_optimize_start(project_root: str, proposal_count: int, log_dir: str = DEFAULT_LOG_DIR) -> None:
    """记录优化流程开始。

    Args:
        project_root: 项目根目录。
        proposal_count: 本次生成的提案总数。
    """
    import datetime

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="optimize_start",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            payload={"proposalCount": proposal_count},
        ),
        log_dir,
    )


def log_optimize_complete(
    project_root: str,
    executed: int,
    succeeded: int,
    failed: int,
    reverted: int,
    log_dir: str = DEFAULT_LOG_DIR,
) -> None:
    """记录优化流程完成。

    Args:
        project_root: 项目根目录。
        executed: 已执行的提案数。
        succeeded: 成功数。
        failed: 失败数。
        reverted: 回滚数。
    """
    import datetime

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="optimize_complete",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            payload={
                "executed": executed,
                "succeeded": succeeded,
                "failed": failed,
                "reverted": reverted,
            },
        ),
        log_dir,
    )


def log_proposal_executed(
    project_root: str,
    result: OptimizationResult,
    log_dir: str = DEFAULT_LOG_DIR,
) -> None:
    """记录提案执行结果。

    Args:
        project_root: 项目根目录。
        result: 优化执行结果。
    """
    import datetime

    from .types import TestSummary

    test_summary_dict = None
    if result.test_summary:
        test_summary_dict = {
            "total": result.test_summary.total,
            "passed": result.test_summary.passed,
            "failed": result.test_summary.failed,
        }

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="proposal_executed",
            timestamp=result.timestamp or datetime.datetime.now(datetime.UTC).isoformat(),
            proposal_id=result.proposal_id,
            payload={
                "status": result.status,
                "totalDurationSeconds": result.total_duration_seconds,
                "fixAttempts": result.fix_attempts,
                "reverted": result.reverted,
                "testSummary": test_summary_dict,
                "lesson": result.lesson,
            },
        ),
        log_dir,
    )


def log_test_run(
    project_root: str,
    test_case_id: str,
    passed: bool,
    duration_ms: float,
    log_dir: str = DEFAULT_LOG_DIR,
) -> None:
    """记录测试执行。"""
    import datetime

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="test_run",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            test_case_id=test_case_id,
            payload={"passed": passed, "durationMs": duration_ms},
        ),
        log_dir,
    )


def log_fix_attempt(
    project_root: str,
    proposal_id: str,
    attempt: int,
    success: bool,
    log_dir: str = DEFAULT_LOG_DIR,
) -> None:
    """记录修复尝试。"""
    import datetime

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="fix_attempted",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            proposal_id=proposal_id,
            payload={"attempt": attempt, "success": success},
        ),
        log_dir,
    )


def log_rollback(
    project_root: str,
    proposal_id: str,
    reason: str,
    success: bool,
    log_dir: str = DEFAULT_LOG_DIR,
) -> None:
    """记录回滚。"""
    import datetime

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="rollback",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            proposal_id=proposal_id,
            payload={"reason": reason, "success": success},
        ),
        log_dir,
    )


def log_error(
    project_root: str,
    error: str,
    proposal_id: str | None = None,
    context: dict[str, Any] | None = None,
    log_dir: str = DEFAULT_LOG_DIR,
) -> None:
    """记录运行时错误。"""
    import datetime

    _append_structured_log(
        project_root,
        StructuredLogEntry(
            type="error",
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            proposal_id=proposal_id,
            payload={"error": error, "context": context or {}},
        ),
        log_dir,
    )


def load_optimization_log(
    project_root: str,
    log_dir: str = DEFAULT_LOG_DIR,
) -> list[OptimizationLogEntry]:
    """读取所有结构化日志，转换为 OptimizationLogEntry[]。

    只转换 `proposal_executed` 事件（与旧格式兼容），
    其他事件类型会被跳过。

    Args:
        project_root: 项目根目录。
        log_dir: 日志目录。

    Returns:
        OptimizationLogEntry 列表。
    """
    log_path = _get_log_path(project_root, log_dir)
    if not os.path.exists(log_path):
        return []

    entries: list[OptimizationLogEntry] = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    structured = json.loads(line)
                    if structured.get("type") == "proposal_executed" and structured.get("proposalId"):
                        payload = structured.get("payload", {})
                        test_summary_data = payload.get("testSummary")
                        test_summary = None
                        if test_summary_data:
                            from .types import TestSummary
                            test_summary = TestSummary(
                                total=test_summary_data.get("total", 0),
                                passed=test_summary_data.get("passed", 0),
                                failed=test_summary_data.get("failed", 0),
                            )

                        from .types import OptimizationResult

                        entries.append(OptimizationLogEntry(
                            result=OptimizationResult(
                                proposal_id=structured["proposalId"],
                                status=payload.get("status", "success"),
                                test_results=[],
                                test_summary=test_summary,
                                fix_attempts=payload.get("fixAttempts", 0),
                                reverted=payload.get("reverted", False),
                                lesson=payload.get("lesson", ""),
                                timestamp=structured.get("timestamp", ""),
                                total_duration_seconds=payload.get("totalDurationSeconds", 0.0),
                            ),
                            proposal_id=structured["proposalId"],
                            proposal_type=payload.get("target", "add"),
                            proposal_target=payload.get("target", structured["proposalId"]),
                            proposal_description=payload.get("lesson", ""),
                            proposal_risk_level=payload.get("riskLevel", "low"),
                        ))
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass

    return entries


def read_raw_structured_log(
    project_root: str,
    log_dir: str = DEFAULT_LOG_DIR,
) -> list[dict[str, Any]]:
    """读取原始结构化日志条目（保留所有事件类型）。

    Args:
        project_root: 项目根目录。
        log_dir: 日志目录。

    Returns:
        原始结构化日志条目列表。
    """
    log_path = _get_log_path(project_root, log_dir)
    if not os.path.exists(log_path):
        return []

    entries: list[dict[str, Any]] = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return entries


def filter_structured_log(
    project_root: str,
    event_type: str,
    log_dir: str = DEFAULT_LOG_DIR,
) -> list[dict[str, Any]]:
    """按事件类型过滤日志。"""
    all_entries = read_raw_structured_log(project_root, log_dir)
    return [e for e in all_entries if e.get("type") == event_type]
