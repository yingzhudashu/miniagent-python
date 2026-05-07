"""Mini Agent Python — ReAct 循环执行器 (Phase 4)

Phase 2 核心：执行结构化计划，实现 ReAct 循环（Think → Act → Observe）。

工作流程：
1. 根据 plan.requiredToolboxes 筛选工具
2. 初始化循环检测器 / 上下文管理器
3. 注入三层记忆
4. ReAct 循环：LLM 调用 → 工具执行 → 结果反馈
5. 循环直到：LLM 不再调用工具 / 达到 maxTurns / 循环检测拦截
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from src.types.planning import StructuredPlan
from src.types.config import AgentConfig
from src.types.tool import ToolContext, ToolRegistryProtocol
from src.types.agent import ToolMonitorProtocol, LoopDetectionConfig
from src.core.config import DEFAULT_LOOP_DETECTION, get_default_model_config
from src.core.logger import append_log, truncate, get_logger

_logger = get_logger(__name__)
from src.core.loop_detector import LoopDetector
from src.core.context_manager import DefaultContextManager
from src.core.memory_store import memory_store, extract_facts, generate_turn_summary
from src.core.keyword_index import search_relevant_memory, format_search_results, get_index_stats
from src.core.loop_detector import LoopDetector
from src.security.sandbox import get_default_workspace

# ─── Agent 身份 ────────────────────────────────────────────

AGENT_NAME = "MiniAgent"
AGENT_IDENTITY = (
    f"你是 {AGENT_NAME}，一个基于 Python 的轻量级 LLM Agent。"
    "你具备两阶段规划（Plan → Execute）、ReAct 循环执行、"
    "工具箱调用、技能加载和自我优化能力。"
    "回答时保持专业、简洁、高效。"
)

# ─── 共享 OpenAI 客户端 ──────────────────────────────────

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """获取全局共享 AsyncOpenAI 客户端。"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
    return _client


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall = Callable[[str, str, str], None]  # (name, args_json, result)
OnThinking = Callable[[str], Awaitable[None]]  # (thinking_text)


# ─── 核心：执行计划 ─────────────────────────────────────

async def execute_plan(
    plan: StructuredPlan,
    user_input: str,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol,
    agent_config: AgentConfig,
    on_tool_call: OnToolCall | None = None,
    on_thinking: OnThinking | None = None,
) -> str:
    """执行结构化计划（ReAct 循环）。

    Args:
        plan: 来自 Phase 1 的结构化执行计划
        user_input: 用户原始需求
        registry: 工具注册表
        monitor: 性能监控器
        agent_config: 合并后的 Agent 配置
        on_tool_call: 工具调用回调

    Returns:
        LLM 的最终回复文本
    """
    # ── 工具筛选 ──
    effective_registry = agent_config.session_registry or registry
    if agent_config.tool_selection_strategy == "all":
        tools = effective_registry.get_schemas()
    else:
        tools = effective_registry.get_schemas_by_toolboxes(plan.required_toolboxes)

    # ── 执行上下文 ──
    workspace = agent_config.session_workspace or get_default_workspace()
    ctx = ToolContext(
        cwd=workspace,
        allowed_paths=[workspace],
        permission="allowlist",
    )

    # ── 循环检测器 ──
    loop_config_data = agent_config.loop_detection or DEFAULT_LOOP_DETECTION
    loop_config = LoopDetectionConfig(**loop_config_data) if isinstance(loop_config_data, dict) else loop_config_data
    loop_detector = LoopDetector(loop_config)

    # ── 上下文管理器 ──
    model_config = get_default_model_config()
    context_manager = DefaultContextManager(
        context_window=model_config.context_window,
        compress_threshold=agent_config.context_compress_threshold,
        tools=tools,
    )

    # ── System prompt + 记忆注入 ──
    system_prompt = f"{AGENT_IDENTITY}\n\n当前任务：{plan.summary}"

    if agent_config.session_key:
        memory = await memory_store.load(agent_config.session_key)

        # Layer 3: 语义检索
        relevant = search_relevant_memory(user_input, top_k=8, min_score=0)
        search_text = format_search_results(relevant)
        if search_text:
            system_prompt += f"\n\n{search_text}"
            if agent_config.debug:
                _logger.debug("Layer 3 语义检索: %d 条相关记忆", len(relevant))

        context_manager.init(system_prompt, user_input)
        if memory:
            context_manager.inject_memory(memory)
    else:
        context_manager.init(system_prompt, user_input)

    # ── 恢复对话历史（在当前输入之前） ──
    if agent_config.conversation_history:
        # 先保存当前 user_input
        current_user_msg = {"role": "user", "content": user_input}
        # 重建消息：system + 历史 + 当前输入
        context_manager._messages = [
            context_manager._messages[0],  # system prompt
            *agent_config.conversation_history,  # 历史消息
            current_user_msg,  # 当前输入
        ]
        context_manager._recalculate_tokens()
        if agent_config.debug:
            _logger.debug("恢复对话历史: %d 条消息", len(agent_config.conversation_history))

    max_turns = agent_config.max_turns
    turns_left = max_turns
    loop_warning_shown = False

    # 跟踪工具调用
    turn_tool_calls: list[dict[str, Any]] = []
    final_reply = ""

    if agent_config.debug:
        idx_stats = get_index_stats()
        _logger.info("使用 %d 个工具 (策略: %s)", len(tools), agent_config.tool_selection_strategy)
        _logger.info("计划: %s", plan.summary)
        _logger.info("最大轮数: %d | 循环检测: %s", max_turns, '启用' if loop_config.enabled else '禁用')
        _logger.debug("三层记忆: L3(关键词索引 %d 词)", idx_stats['total_keywords'])

    client = get_client()

    # ── ReAct 循环 ──
    while turns_left > 0:
        turns_left -= 1
        start_ms = time.monotonic_ns() // 1_000_000
        messages = context_manager.get_messages()

        if agent_config.debug:
            _logger.debug("LLM 请求 (第 %d 轮): 消息数=%d, 工具数=%d", max_turns - turns_left, len(messages), len(tools))

        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,  # type: ignore[arg-type]
            tools=tools if tools else None,  # type: ignore[arg-type]
        )

        msg = response.choices[0].message

        # 增量日志
        if agent_config.log_file:
            append_log(agent_config.log_file, {
                "phase": "exec",
                "turn": max_turns - turns_left,
                "req": {"model": MODEL, "messageCount": len(messages), "toolCount": len(tools)},
                "res": {
                    "hasToolCalls": bool(msg.tool_calls),
                    "toolCalls": [{"name": tc.function.name, "args": truncate(tc.function.arguments, 300)}
                                  for tc in (msg.tool_calls or [])],
                    "content": truncate(msg.content or "", 1000) if msg.content else None,
                    "usage": response.usage.model_dump() if response.usage else None,
                },
            })

        # ── 触发思考回调 ──
        if on_thinking and msg.content:
            try:
                turn_label = f"[第 {max_turns - turns_left} 轮思考]"
                await on_thinking(f"{turn_label}\n{msg.content}")
            except Exception:
                pass  # 思考回调失败不影响主流程

        # ── 无工具调用 → 最终回复 ──
        if not msg.tool_calls:
            final_reply = msg.content or "(空回复)"
            elapsed = time.monotonic_ns() // 1_000_000 - start_ms
            monitor.record("llm_response", elapsed, True)
            context_manager.append({"role": "assistant", "content": final_reply})

            if agent_config.session_key and final_reply:
                await _save_session_memory(
                    agent_config.session_key, user_input, final_reply, turn_tool_calls
                )

            if agent_config.debug:
                _logger.debug(context_manager.get_token_report())

            return final_reply

        # 追加 LLM 回复到上下文
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        context_manager.append(assistant_msg)

        # ── 按顺序执行每个工具调用 ──
        for tc in msg.tool_calls:
            tool = registry.get(tc.function.name)
            if tool is None:
                context_manager.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"错误：未知工具 {tc.function.name}。可用: {', '.join(registry.list())}",
                })
                if on_tool_call:
                    on_tool_call(tc.function.name, tc.function.arguments, "⚠️ 未知工具")
                continue

            # ── 循环检测 ──
            try:
                args = json.loads(tc.function.arguments)
                loop_check = loop_detector.check(tc.function.name, args)

                if loop_check.level == "critical":
                    elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                    monitor.record(tc.function.name, elapsed, False)
                    _logger.warning("循环检测拦截: %s", loop_check.message)
                    return f"⚠️ 任务执行被终止：{loop_check.message}\n\n建议：简化请求或明确具体目标。"

                if loop_check.level == "warning" and not loop_warning_shown:
                    loop_warning_shown = True
                    _logger.warning(loop_check.message)
            except Exception:
                args = {}

            # ── 执行工具 ──
            tool_start = time.monotonic_ns() // 1_000_000
            try:
                result = await tool.handler(args, ctx)
                turn_tool_calls.append({
                    "name": tc.function.name,
                    "args": tc.function.arguments,
                    "result": result.content,
                })
                loop_detector.record(tc.function.name, args, result.content)
            except Exception as e:
                from src.types.tool import ToolResult
                result = ToolResult(
                    success=False,
                    content=f"⚠️ 执行异常: {e}",
                )
                turn_tool_calls.append({"name": tc.function.name, "args": tc.function.arguments})

            tool_elapsed = time.monotonic_ns() // 1_000_000 - tool_start
            monitor.record(tc.function.name, tool_elapsed, result.success)
            context_manager.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result.content,
            })
            if on_tool_call:
                on_tool_call(tc.function.name, tc.function.arguments, result.content)

            # 工具执行结果也推给思考回调
            if on_thinking:
                try:
                    preview = result.content[:200]
                    await on_thinking(f"🔧 {tc.function.name} → {preview}")
                except Exception:
                    pass  # 思考回调失败不影响主流程

    # ── 达到最大轮数 ──
    loop_stats = loop_detector.get_stats()

    if agent_config.session_key:
        await _save_session_memory(
            agent_config.session_key, user_input, "达到最大轮数，任务未完成", turn_tool_calls
        )

    if agent_config.debug:
        _logger.debug(context_manager.get_token_report())

    return (
        f"⚠️ 达到最大调用次数（{max_turns} 轮），任务未完成。\n\n"
        f"建议：简化请求，分步骤执行。\n\n"
        f"📊 本轮统计：工具调用 {loop_stats['total_calls']} 次"
    )


# ─── 记忆保存 ────────────────────────────────────────────

async def _save_session_memory(
    session_key: str,
    user_input: str,
    final_reply: str,
    turn_tool_calls: list[dict[str, Any]],
) -> None:
    """保存会话记忆：提取事实、生成摘要、写入存储。"""
    from datetime import datetime, timezone

    facts = extract_facts(user_input + " " + final_reply)
    summary = generate_turn_summary(user_input, turn_tool_calls, final_reply)
    now = datetime.now(timezone.utc).isoformat()

    await memory_store.update_summary(session_key, summary, facts)
    await memory_store.add_entry(session_key, {
        "timestamp": now,
        "user_snippet": user_input[:100],
        "summary": summary,
        "facts": facts,
    })


__all__ = ["execute_plan", "get_client", "MODEL", "AGENT_NAME", "AGENT_IDENTITY"]
