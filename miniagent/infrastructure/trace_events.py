"""Trace 事件类型定义与标准化字段。

定义全链路 Trace 事件类型常量、标准字段格式、性能指标采集规范。
供 emit_trace() 调用方统一事件结构，便于后续统计分析。

事件类型规范：
- 使用点号分隔命名空间：{namespace}.{action}
- 现有类型来自 executor.py，保持兼容
- 新增类型遵循统一命名规范

标准字段规范：
- ts: 时间戳（ISO 8601，由 emit_trace 自动添加）
- type: 事件类型
- session_key: 会话标识
- phase: 执行阶段（plan/exec）
- duration_ms: 时延（毫秒）
- success: 是否成功
- error_type: 错误类型（可选）

详见 docs/ENGINEERING.md（Trace 系统）。
"""

from __future__ import annotations

from typing import Literal

# ============================================================================
# 事件类型常量
# ============================================================================

# 会话生命周期事件已删除（未在生产代码中使用）
# EVENT_SESSION_START = "session.start"
# EVENT_SESSION_END = "session.end"

# 现有事件类型（executor.py 中已使用）
EVENT_LLM_REQUEST = "llm.request"
EVENT_LLM_RESPONSE = "llm.response"
EVENT_TOOL_START = "tool.start"
EVENT_TOOL_END = "tool.end"
EVENT_TOOL_ERROR = "tool.error"

# 新增事件类型 - 记忆操作（已使用）
EVENT_MEMORY_READ = "memory.read"

# 新增事件类型 - 上下文管理（已使用）
EVENT_CONTEXT_COMPRESS = "context.compress"

# 新增事件类型 - 自优化（已使用）
EVENT_PROPOSAL_CREATE = "proposal.create"
EVENT_PROPOSAL_APPROVE = "proposal.approve"
EVENT_PROPOSAL_REJECT = "proposal.reject"
EVENT_PROPOSAL_APPLY = "proposal.apply"

# 新增事件类型 - 错误收集（已使用）
EVENT_ERROR_COLLECT = "error.collect"

# ============================================================================
# 执行阶段常量
# ============================================================================

PhaseType = Literal["plan", "exec", "init", "shutdown"]

# ============================================================================
# 提案状态常量
# ============================================================================

ProposalStatus = Literal["pending", "approved", "rejected", "executing", "completed", "failed"]

# ============================================================================
# 提案来源常量
# ============================================================================

ProposalSource = Literal["code_analysis", "runtime_analysis", "manual", "scheduled"]

# ============================================================================
# 风险等级常量
# ============================================================================

RiskLevel = Literal["low", "medium", "high"]

# ============================================================================
# 标准事件字段模板
# ============================================================================


def make_proposal_event(
    event_type: str,
    proposal_id: str,
    source: ProposalSource,
    risk_level: RiskLevel,
    description: str | None = None,
    duration_ms: int | None = None,
    result: str | None = None,
) -> dict:
    """创建提案事件的标准结构。

    Args:
        event_type: 事件类型
        proposal_id: 提案 ID
        source: 提案来源
        risk_level: 风险等级
        description: 提案描述
        duration_ms: 执行耗时
        result: 执行结果

    Returns:
        标准化的事件字典
    """
    event = {
        "type": event_type,
        "proposal_id": proposal_id,
        "source": source,
        "risk_level": risk_level,
    }
    if description:
        event["description_preview"] = description[:200] if len(description) > 200 else description
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    if result:
        event["result"] = result
    return event


def make_error_event(
    session_key: str,
    error_type: str,
    error_message: str,
    location: str | None = None,
    tool_name: str | None = None,
    is_user_error: bool = False,
) -> dict:
    """创建错误收集事件的标准结构。

    Args:
        session_key: 会话标识
        error_type: 错误类型
        error_message: 错误消息
        location: 发生位置
        tool_name: 相关工具名（工具错误时）
        is_user_error: 是否为用户误用

    Returns:
        标准化的事件字典
    """
    event = {
        "type": EVENT_ERROR_COLLECT,
        "session_key": session_key,
        "error_type": error_type,
        "error_message": error_message[:500] if len(error_message) > 500 else error_message,
        "is_user_error": is_user_error,
    }
    if location:
        event["location"] = location
    if tool_name:
        event["tool_name"] = tool_name
    return event


__all__ = [
    # 事件类型常量
    "EVENT_LLM_REQUEST",
    "EVENT_LLM_RESPONSE",
    "EVENT_TOOL_START",
    "EVENT_TOOL_END",
    "EVENT_TOOL_ERROR",
    "EVENT_MEMORY_READ",
    "EVENT_CONTEXT_COMPRESS",
    "EVENT_PROPOSAL_CREATE",
    "EVENT_PROPOSAL_APPROVE",
    "EVENT_PROPOSAL_REJECT",
    "EVENT_PROPOSAL_APPLY",
    "EVENT_ERROR_COLLECT",
    # 类型常量
    "PhaseType",
    "ProposalStatus",
    "ProposalSource",
    "RiskLevel",
    # 事件构建函数
    "make_proposal_event",
    "make_error_event",
]