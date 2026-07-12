"""ReAct 执行前的工具沙箱、循环检测和上下文装配。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
from miniagent.contracts.memory import MemoryRuntimeProtocol
from miniagent.core.config import get_default_agent_config, get_default_model_config
from miniagent.core.execution_prompts import (
    build_current_turn_user_context,
    build_stable_execution_system_prompt,
)
from miniagent.core.plan_utils import (
    format_output_spec_block,
    resolve_chunk_compress_threshold,
    resolve_effective_overflow_strategy,
)
from miniagent.core.prompts.identity import AGENT_IDENTITY
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.loop_detector import LoopDetector
from miniagent.infrastructure.timezone_config import format_agent_timezone_context
from miniagent.memory.context import DefaultContextManager
from miniagent.memory.history_bridge import conversation_history_for_llm
from miniagent.security.sandbox import get_default_workspace
from miniagent.types.agent import LoopDetectionConfig
from miniagent.types.config import AgentConfig
from miniagent.types.planning import StructuredPlan
from miniagent.types.skill import ClawHubClientProtocol
from miniagent.types.tool import ToolContext

_logger = get_logger(__name__)


def build_tool_context(
    agent_config: AgentConfig,
    *,
    clawhub: ClawHubClientProtocol | None,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    receive_id_type_resolver: Any,
) -> ToolContext:
    """构造受工作区 allowlist 约束的工具上下文。"""
    session = agent_config.session_config
    feishu = agent_config.feishu_config
    workspace = session.session_workspace or get_default_workspace()
    return ToolContext(
        cwd=workspace,
        allowed_paths=list(dict.fromkeys([workspace, os.getcwd()])),
        permission="allowlist",
        clawhub=clawhub,
        knowledge_registry=knowledge_registry,
        llm_client=client,
        session_key=session.session_key,
        cli_loop_state=feishu.cli_loop_state,
        cli_dispatch_allow_mutations=feishu.cli_dispatch_allow_mutations,
        message_queue_abort_chat_id=(feishu.receive_chat_id or "").strip() or None,
        feishu_im_receive_id_type=receive_id_type_resolver(feishu.im_receive_id_type or ""),
        feishu_im_receive_id=(feishu.im_receive_id or "").strip() or None,
    )


def build_loop_detector(agent_config: AgentConfig) -> tuple[LoopDetector, LoopDetectionConfig]:
    """从强类型配置或兼容字典构造本轮循环检测器。"""
    value = agent_config.loop_detection or get_default_agent_config().loop_detection
    config = LoopDetectionConfig(**value) if isinstance(value, dict) else value
    return LoopDetector(config), config


async def build_execution_context(
    plan: StructuredPlan,
    user_input: str,
    *,
    tools: list[dict[str, Any]],
    agent_config: AgentConfig,
    memory: MemoryRuntimeProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    system_prompt: str | None,
    ephemeral_resolver: Any,
) -> tuple[DefaultContextManager, bool, bool]:
    """注入本轮记忆与知识，并按稳定前缀顺序恢复会话历史。"""
    from miniagent.knowledge import retrieve_knowledge_context

    session = agent_config.session_config
    model = get_default_model_config()
    context = DefaultContextManager(
        context_window=model.context_window,
        compress_threshold=resolve_chunk_compress_threshold(
            plan, context_window=model.context_window,
            default_threshold=agent_config.context_compress_threshold,
        ),
        tools=tools,
        overflow_strategy=resolve_effective_overflow_strategy(
            plan, agent_config.context_overflow_strategy
        ),
        reserve_ratio=agent_config.context_reserve_ratio,
        session_key=session.session_key,
    )
    ephemeral = ephemeral_resolver(session.session_key)
    activity_enabled = bool(session.session_key) and not ephemeral
    keyword_context: str | None = None
    if session.session_key and not ephemeral:
        _, metadata = await memory.context.inject_memory_to_messages(
            [], session.session_key, agent_config, user_input=user_input,
            activity_log=memory.activity_log, keyword_index=memory.keyword_index,
        )
        keyword_context = metadata.get("turn_keyword_context")
        if agent_config.debug and metadata.get("relevant_count"):
            _logger.debug("Layer 3 语义检索: %d 条相关记忆", metadata["relevant_count"])
    knowledge = retrieve_knowledge_context(
        knowledge_registry, user_input, phase="executor", default_top_k=3, default_max_chars=4000
    )
    current_user = build_current_turn_user_context(
        user_input=user_input, plan_summary=plan.summary, keyword_context=keyword_context,
        kb_context=knowledge or None, session_files_root=session.session_workspace,
        risk_level=agent_config.risk_level, current_time_context=format_agent_timezone_context(),
        output_spec_block=format_output_spec_block(plan.output_spec),
    )
    context.init(
        build_stable_execution_system_prompt(
            agent_identity=AGENT_IDENTITY, caller_system_prompt=system_prompt
        ),
        current_user,
    )
    if session.conversation_history:
        history = conversation_history_for_llm(session.conversation_history)
        context._messages = [
            context._messages[0], *history, {"role": "user", "content": current_user}
        ]
        context._recalculate_tokens()
        if agent_config.debug:
            _logger.debug("恢复对话历史: %d 条消息", len(session.conversation_history))
    return context, ephemeral, activity_enabled


__all__ = ["build_execution_context", "build_loop_detector", "build_tool_context"]
