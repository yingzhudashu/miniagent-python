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

from typing import Any, Literal

TRACE_SCHEMA_VERSION = 1

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

# 新增事件类型 - 需求澄清（已使用）
EVENT_REQUIREMENT_CLARIFY = "requirement.clarify"

# 新增事件类型 - 知识库文件入库（已使用）
EVENT_KNOWLEDGE_FILE_INGEST = "knowledge.file_ingest"

# 新增事件类型 - 上下文管理（已使用）
EVENT_CONTEXT_COMPRESS = "context.compress"

# 新增事件类型 - 自优化（已使用）
EVENT_PROPOSAL_CREATE = "proposal.create"
EVENT_PROPOSAL_APPROVE = "proposal.approve"
EVENT_PROPOSAL_REJECT = "proposal.reject"
EVENT_PROPOSAL_APPLY = "proposal.apply"

# 新增事件类型 - 错误收集（已使用）
EVENT_ERROR_COLLECT = "error.collect"

# 新增事件类型 - 浏览器实例管理（性能优化）
EVENT_BROWSER_CREATE = "browser.create"
EVENT_BROWSER_REUSE = "browser.reuse"
EVENT_BROWSER_CLOSE = "browser.close"

# 新增事件类型 - Embedding缓存（性能优化）
EVENT_EMBEDDING_CACHE_HIT = "embedding.cache_hit"
EVENT_EMBEDDING_API_CALL = "embedding.api_call"
EVENT_FEISHU_DOCX_RENDER = "feishu.docx_render"

# Agent / performance lifecycle events.  These are additive so existing trace
# consumers can continue to ignore event types they do not understand.
EVENT_AGENT_RUN_START = "agent.run_start"
EVENT_AGENT_RUN_END = "agent.run_end"
EVENT_AGENT_PHASE_START = "agent.phase_start"
EVENT_AGENT_PHASE_END = "agent.phase_end"
EVENT_PERF_RESOURCE_SAMPLE = "perf.resource_sample"
EVENT_EMBEDDING_INDEX_QUEUED = "embedding.index_queued"
EVENT_EMBEDDING_INDEX_COMPLETED = "embedding.index_completed"

TRACE_EVENT_TYPES = frozenset(
    {
        EVENT_AGENT_PHASE_END,
        EVENT_AGENT_PHASE_START,
        EVENT_AGENT_RUN_END,
        EVENT_AGENT_RUN_START,
        EVENT_BROWSER_CLOSE,
        EVENT_BROWSER_CREATE,
        EVENT_BROWSER_REUSE,
        EVENT_CONTEXT_COMPRESS,
        EVENT_EMBEDDING_API_CALL,
        EVENT_EMBEDDING_CACHE_HIT,
        EVENT_EMBEDDING_INDEX_COMPLETED,
        EVENT_EMBEDDING_INDEX_QUEUED,
        EVENT_ERROR_COLLECT,
        EVENT_FEISHU_DOCX_RENDER,
        EVENT_KNOWLEDGE_FILE_INGEST,
        EVENT_LLM_REQUEST,
        EVENT_LLM_RESPONSE,
        EVENT_MEMORY_READ,
        EVENT_PERF_RESOURCE_SAMPLE,
        EVENT_PROPOSAL_APPLY,
        EVENT_PROPOSAL_APPROVE,
        EVENT_PROPOSAL_CREATE,
        EVENT_PROPOSAL_REJECT,
        EVENT_REQUIREMENT_CLARIFY,
        EVENT_TOOL_END,
        EVENT_TOOL_ERROR,
        EVENT_TOOL_START,
    }
)

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
    event: dict[str, Any] = {
        "type": event_type,
        "proposal_id": proposal_id,
        "source": source,
        "risk_level": risk_level,
    }
    if description:
        # 切片超出长度时返回完整字符串，无需额外的长度判断
        event["description_preview"] = description[:200]
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
        "error_message": error_message[:500],
        "is_user_error": is_user_error,
    }
    if location:
        event["location"] = location
    if tool_name:
        event["tool_name"] = tool_name
    return event


__all__ = [
    "TRACE_SCHEMA_VERSION",
    "TRACE_EVENT_TYPES",
    # 事件类型常量
    "EVENT_LLM_REQUEST",
    "EVENT_LLM_RESPONSE",
    "EVENT_TOOL_START",
    "EVENT_TOOL_END",
    "EVENT_TOOL_ERROR",
    "EVENT_MEMORY_READ",
    "EVENT_REQUIREMENT_CLARIFY",
    "EVENT_KNOWLEDGE_FILE_INGEST",
    "EVENT_CONTEXT_COMPRESS",
    "EVENT_PROPOSAL_CREATE",
    "EVENT_PROPOSAL_APPROVE",
    "EVENT_PROPOSAL_REJECT",
    "EVENT_PROPOSAL_APPLY",
    "EVENT_ERROR_COLLECT",
    # 新增性能优化事件类型
    "EVENT_BROWSER_CREATE",
    "EVENT_BROWSER_REUSE",
    "EVENT_BROWSER_CLOSE",
    "EVENT_EMBEDDING_CACHE_HIT",
    "EVENT_EMBEDDING_API_CALL",
    "EVENT_FEISHU_DOCX_RENDER",
    "EVENT_AGENT_RUN_START",
    "EVENT_AGENT_RUN_END",
    "EVENT_AGENT_PHASE_START",
    "EVENT_AGENT_PHASE_END",
    "EVENT_PERF_RESOURCE_SAMPLE",
    "EVENT_EMBEDDING_INDEX_QUEUED",
    "EVENT_EMBEDDING_INDEX_COMPLETED",
    # 类型常量
    "PhaseType",
    "ProposalStatus",
    "ProposalSource",
    "RiskLevel",
    # 事件构建函数
    "make_proposal_event",
    "make_error_event",
]
