"""运行时错误收集器 (Phase 5.2 新增)

Self-Optimization 子系统的核心组件之一。收集运行时错误并持久化到日志文件，
为错误分析引擎（error_analyzer.py）提供数据源。

工作流程：
1. Agent/Tool 执行时捕获到错误，调用 collect_error()
2. 错误被序列化为 JSON Line，追加写入 errors/error-log.jsonl
3. error_analyzer.py 定期读取日志，聚类分析，生成修复方案

设计原则：
- 写入不阻塞主流程
- JSON Lines 格式，每行一个独立错误记录
- 自动去重计数（相同错误类型+堆栈 hash）
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 默认配置
DEFAULT_LOG_DIR = "errors"
DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@dataclass
class ErrorContext:
    """错误上下文。"""
    tool: str | None = None
    input: str | None = None
    proposal_id: str | None = None
    meta: dict[str, Any] | None = None


@dataclass
class RuntimeErrorRecord:
    """运行时错误记录。"""
    id: str
    timestamp: str
    error_type: str
    message: str
    stack: str
    stack_hash: str
    context: ErrorContext
    occurrence_count: int = 1


def _compute_stack_hash(stack: str) -> str:
    """计算堆栈哈希（用于去重）。"""
    normalized = "\n".join(
        line.replace(":", ":0:0") for line in stack.split("\n")[:5]
    )
    return hashlib.md5(normalized.encode()).hexdigest()[:8]


def _truncate(s: str, max_len: int) -> str:
    """截断字符串到指定长度。"""
    return s if len(s) <= max_len else s[:max_len] + "...(truncated)"


def _get_log_file_path(log_dir: str) -> str:
    """获取错误日志文件路径。"""
    import datetime
    suffix = datetime.datetime.now().strftime("%Y-%m-%d")
    return os.path.join(log_dir, f"error-log-{suffix}.jsonl")


def _count_occurrences(log_path: str, stack_hash: str) -> int:
    """读取已有的错误日志，统计每个 stack_hash 的出现次数。"""
    if not os.path.exists(log_path):
        return 0
    try:
        count = 0
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("stackHash") == stack_hash or record.get("stack_hash") == stack_hash:
                        count += 1
                except json.JSONDecodeError:
                    continue
        return count
    except Exception:
        return 0


def collect_error(
    error: Exception,
    context: ErrorContext | None = None,
    log_dir: str | None = None,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
) -> RuntimeErrorRecord:
    """收集运行时错误。

    将错误信息序列化并追加到错误日志文件。

    Args:
        error: 捕获到的 Exception 对象。
        context: 错误发生的上下文。
        log_dir: 日志目录（默认：当前目录/errors）。
        max_file_size: 最大单文件大小。

    Returns:
        错误记录。
    """
    import datetime

    if context is None:
        context = ErrorContext()

    log_dir = log_dir or os.path.join(os.getcwd(), DEFAULT_LOG_DIR)
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    stack = str(error.__traceback__) if error.__traceback__ else ""
    if not stack:
        import traceback
        stack = traceback.format_exc()

    stack_hash = _compute_stack_hash(stack)
    log_path = _get_log_file_path(log_dir)

    occurrence_count = _count_occurrences(log_path, stack_hash) + 1

    record = RuntimeErrorRecord(
        id=f"err-{int(datetime.datetime.now().timestamp() * 1000)}-{stack_hash[:4]}",
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        error_type=type(error).__name__ or "Error",
        message=_truncate(str(error), 1000),
        stack=_truncate("\n".join(stack.split("\n")[:10]), 2000),
        stack_hash=stack_hash,
        context=context,
        occurrence_count=occurrence_count,
    )

    line = json.dumps({
        "id": record.id,
        "timestamp": record.timestamp,
        "errorType": record.error_type,
        "message": record.message,
        "stack": record.stack,
        "stackHash": record.stack_hash,
        "context": {
            "tool": record.context.tool,
            "input": record.context.input,
            "proposalId": record.context.proposal_id,
            "meta": record.context.meta,
        },
        "occurrenceCount": record.occurrence_count,
    }, ensure_ascii=False) + "\n"

    try:
        if os.path.exists(log_path):
            stats = os.stat(log_path)
            if stats.st_size > max_file_size:
                rotated_path = log_path + ".bak"
                os.rename(log_path, rotated_path)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[runtime-error-collector] 写入错误日志失败: {e}")

    return record


def collect_errors(
    errors: list[tuple[Exception, ErrorContext | None]],
    log_dir: str | None = None,
) -> list[RuntimeErrorRecord]:
    """批量收集错误。"""
    return [collect_error(e, ctx, log_dir) for e, ctx in errors]


def parse_error_log(
    log_path: str | None = None,
    log_dir: str | None = None,
    limit: int = 50,
) -> list[RuntimeErrorRecord]:
    """读取错误日志文件，解析为记录列表。

    Args:
        log_path: 日志文件路径（如果提供，忽略 log_dir）。
        log_dir: 日志目录。
        limit: 最大返回条数。

    Returns:
        错误记录列表。
    """
    if log_path is None:
        log_dir = log_dir or os.path.join(os.getcwd(), DEFAULT_LOG_DIR)
        log_path = _get_log_file_path(log_dir)

    if not os.path.exists(log_path):
        return []

    records: list[RuntimeErrorRecord] = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                ctx_data = data.get("context", {})
                record = RuntimeErrorRecord(
                    id=data.get("id", ""),
                    timestamp=data.get("timestamp", ""),
                    error_type=data.get("errorType", "Error"),
                    message=data.get("message", ""),
                    stack=data.get("stack", ""),
                    stack_hash=data.get("stackHash", ""),
                    context=ErrorContext(
                        tool=ctx_data.get("tool"),
                        input=ctx_data.get("input"),
                        proposal_id=ctx_data.get("proposalId"),
                        meta=ctx_data.get("meta"),
                    ),
                    occurrence_count=data.get("occurrenceCount", 1),
                )
                records.append(record)
                if len(records) >= limit:
                    break
            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    return records


def detect_frequent_errors(
    threshold: int = 5,
    log_dir: str | None = None,
) -> list[str]:
    """检查是否存在高频错误。

    Args:
        threshold: 触发阈值。
        log_dir: 日志目录。

    Returns:
        高频错误的 stack_hash 列表。
    """
    log_dir = log_dir or os.path.join(os.getcwd(), DEFAULT_LOG_DIR)
    log_path = _get_log_file_path(log_dir)
    if not os.path.exists(log_path):
        return []

    try:
        counts: dict[str, int] = {}
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    sh = data.get("stackHash", "")
                    counts[sh] = counts.get(sh, 0) + 1
                except json.JSONDecodeError:
                    continue

        return [h for h, c in counts.items() if c >= threshold]
    except Exception:
        return []
