"""Mini Agent Python — ReAct 循环执行器（两阶段中的执行阶段）

执行 Phase 1 产出的结构化计划，实现 ReAct 循环（Think → Act → Observe）。

工作流程：
1. 根据 plan.requiredToolboxes 筛选工具
2. 初始化循环检测器 / 上下文管理器
3. 注入三层记忆
4. ReAct 循环：LLM 调用 → 工具执行 → 结果反馈
5. 循环直到：LLM 不再调用工具 / 达到 maxTurns / 循环检测拦截

``MINIAGENT_PHASED_EXECUTION`` 开启且 ``plan.steps`` 非空时，按步骤分子循环（每步独立 thinking 解析）；
详见环境变量说明与 ``docs/ARCHITECTURE.md``。

**不变量**：工具调用均在 :class:`miniagent.types.tool.ToolContext` 限定的 ``cwd`` / ``allowed_paths`` 内执行
（通常由沙箱默认工作区推导）。上下文 token 超预算时抛出
:class:`miniagent.memory.context.ContextBudgetExceeded`，由上层决定是否换会话或压缩。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from miniagent.core.llm_params import resolve_exec_completion_kwargs
from miniagent.core.openai_client import get_shared_async_openai
from miniagent.core.thinking_presets import map_business_depth
from miniagent.types.memory import MemoryEntryInput
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.config import AgentConfig
from miniagent.types.tool import ToolContext, ToolRegistryProtocol
from miniagent.types.agent import ToolMonitorProtocol, LoopDetectionConfig
from miniagent.core.config import DEFAULT_LOOP_DETECTION, get_default_model_config
from miniagent.infrastructure.logger import append_log, truncate, get_logger
from miniagent.infrastructure.loop_detector import LoopDetector
from miniagent.infrastructure.tracing import emit_trace
from miniagent.memory.context import ContextBudgetExceeded, DefaultContextManager
from miniagent.memory.store import extract_facts, generate_turn_summary
from miniagent.memory.keyword_index import format_search_results, search_relevant_with_index
from miniagent.security.sandbox import get_default_workspace

_logger = get_logger(__name__)

# ─── Agent 身份 ────────────────────────────────────────────

AGENT_NAME = "MiniAgent"
AGENT_IDENTITY = (
    f"你是 {AGENT_NAME}，一个基于 Python 的轻量级 LLM Agent。"
    "你具备两阶段规划（Plan → Execute）、ReAct 循环执行、"
    "工具箱调用、技能加载和自我优化能力。"
    "涉及时效性或客观事实的问题（天气、股价、新闻等）：应先使用 web_search（Tavily）检索；"
    "若页面依赖前端渲染再使用 browser_extract_text；静态 HTML 可优先 fetch_url；"
    "需要「今天/明天」等日期请先调用 get_time。"
    "回答时保持专业、简洁、高效。"
)

# ─── 默认模型名（兼容导出；实际请求参数见 resolve_exec_completion_kwargs）──

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def build_execution_system_prompt(
    *,
    agent_identity: str,
    caller_system_prompt: str | None,
    plan_summary: str,
    keyword_context: str | None,
) -> str:
    """按约定拼接执行阶段 system：身份 → 调用方技能/指令 → 任务摘要 → 关键词检索上下文。"""
    parts: list[str] = [agent_identity.strip()]
    if caller_system_prompt and caller_system_prompt.strip():
        parts.append(caller_system_prompt.strip())
    parts.append(f"当前任务：{plan_summary.strip()}")
    if keyword_context and keyword_context.strip():
        parts.append(keyword_context.strip())
    return "\n\n".join(parts)


def get_client() -> AsyncOpenAI:
    """获取进程内共享 AsyncOpenAI（与 :func:`get_shared_async_openai` 相同）。"""
    return get_shared_async_openai()


def _env_phased_execution_enabled() -> bool:
    v = os.environ.get("MINIAGENT_PHASED_EXECUTION", "1")
    return str(v).strip().lower() in ("1", "true", "yes")


def _step_max_turns_cap() -> int:
    raw = os.environ.get("MINIAGENT_STEP_MAX_TURNS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 5


def _resolve_exec_tools(
    effective_registry: ToolRegistryProtocol,
    agent_config: AgentConfig,
    plan: StructuredPlan,
    step: PlanStep | None,
) -> list[Any]:
    """与主流程一致的工具筛选；``step`` 非空且含 required_toolboxes 时按步骤覆盖。"""
    step_tbs = list(step.required_toolboxes) if step and step.required_toolboxes else None
    plan_tbs = plan.required_toolboxes

    if agent_config.tool_selection_strategy == "all":
        return effective_registry.get_schemas()
    if agent_config.tool_selection_strategy == "auto":
        tbs = step_tbs if step_tbs else plan_tbs
        if tbs:
            return effective_registry.get_schemas_by_toolboxes(tbs)
        tools = [
            t.schema
            for t in effective_registry.get_all().values()
            if t.toolbox is None
        ]
        return tools if tools else effective_registry.get_schemas()
    tbs = step_tbs if step_tbs else plan_tbs
    return effective_registry.get_schemas_by_toolboxes(tbs)


def _append_context_or_return(
    context_manager: DefaultContextManager,
    msg: dict[str, Any],
) -> str | None:
    """追加消息；若 overflow_strategy=error 且超预算则返回错误文案。"""
    try:
        context_manager.append(msg)
    except ContextBudgetExceeded as e:
        return f"⚠️ {e}"
    return None


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall = Callable[[str, str, str], None]  # (name, args_json, result)
OnThinking = Callable[[str, bool, str], Awaitable[None]]  # (thinking_text, streaming, header)


# ─── 核心：执行计划 ─────────────────────────────────────

async def execute_plan(
    plan: StructuredPlan,
    user_input: str,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol,
    agent_config: AgentConfig,
    on_tool_call: OnToolCall | None = None,
    on_thinking: OnThinking | None = None,
    *,
    system_prompt: str | None = None,
    clawhub: Any | None = None,
    memory_store: Any | None = None,
    activity_log: Any | None = None,
    keyword_index: Any | None = None,
    client: AsyncOpenAI | None = None,
) -> str:
    """执行结构化计划（ReAct 循环）。

    Args:
        plan: 来自 Phase 1 的结构化执行计划
        user_input: 用户原始需求
        registry: 工具注册表
        monitor: 性能监控器
        agent_config: 合并后的 Agent 配置
        on_tool_call: 工具调用回调
        memory_store: 记忆存储（默认与 ``MINI_AGENT_STATE`` 进程 bundle 一致）
        activity_log: 活动日志（同上）
        keyword_index: 关键词索引（同上；缺省时优先使用 store 已绑定索引）
        client: LLM 客户端（默认进程内共享 AsyncOpenAI）
        system_prompt: 调用方注入的系统指令（如技能合并文案）；与身份、任务摘要等按序合并

    Returns:
        LLM 的最终回复文本
    """
    from miniagent.memory.defaults import resolve_memory_dependencies

    ms, al, ki = resolve_memory_dependencies(memory_store, activity_log, keyword_index)

    # ── 工具筛选 ──
    effective_registry = agent_config.session_registry or registry
    tools = _resolve_exec_tools(effective_registry, agent_config, plan, None)

    # ── 执行上下文 ──
    workspace = agent_config.session_workspace or get_default_workspace()
    ctx = ToolContext(
        cwd=workspace,
        allowed_paths=[workspace],
        permission="allowlist",
        clawhub=clawhub,
        session_key=getattr(agent_config, "session_key", None),
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
        overflow_strategy=agent_config.context_overflow_strategy,
    )

    # ── System prompt + 记忆注入 ──
    keyword_context: str | None = None
    if agent_config.session_key:
        memory = await ms.load(agent_config.session_key)

        # Layer 3: 语义检索（ki 为注入索引或进程默认 bundle）
        relevant = search_relevant_with_index(ki, user_input, top_k=8, min_score=0)
        search_text = format_search_results(relevant)
        if search_text:
            keyword_context = search_text
            if agent_config.debug:
                _logger.debug("Layer 3 语义检索: %d 条相关记忆", len(relevant))

        merged_system = build_execution_system_prompt(
            agent_identity=AGENT_IDENTITY,
            caller_system_prompt=system_prompt,
            plan_summary=plan.summary,
            keyword_context=keyword_context,
        )
        if agent_config.risk_level:
            merged_system += f"\n\n（本任务风险等级：{agent_config.risk_level}）"
        context_manager.init(merged_system, user_input)
        if memory:
            context_manager.inject_memory(memory)
    else:
        merged_system = build_execution_system_prompt(
            agent_identity=AGENT_IDENTITY,
            caller_system_prompt=system_prompt,
            plan_summary=plan.summary,
            keyword_context=None,
        )
        if agent_config.risk_level:
            merged_system += f"\n\n（本任务风险等级：{agent_config.risk_level}）"
        context_manager.init(merged_system, user_input)

    # ── 恢复对话历史（在当前输入之前） ──
    if agent_config.conversation_history:
        from miniagent.memory.history_bridge import conversation_history_for_llm

        # 先保存当前 user_input
        current_user_msg = {"role": "user", "content": user_input}
        hist_api = conversation_history_for_llm(agent_config.conversation_history)
        # 重建消息：system + 历史 + 当前输入
        context_manager._messages = [
            context_manager._messages[0],  # system prompt
            *hist_api,  # 历史消息（含 thinking → assistant 映射）
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

    # 活动日志 — 记录会话开始
    session_key = agent_config.session_key or "default"
    source = "cli"  # 默认 CLI，飞书调用方会设置 session_key
    al.log_session_start(session_key, user_input, source)

    if agent_config.debug:
        idx_stats = ki.get_stats()
        _logger.info("使用 %d 个工具 (策略: %s)", len(tools), agent_config.tool_selection_strategy)
        _logger.info("计划: %s", plan.summary)
        _logger.info("最大轮数: %d | 循环检测: %s", max_turns, '启用' if loop_config.enabled else '禁用')
        _logger.debug("三层记忆: L3(关键词索引 %d 词)", idx_stats['total_keywords'])

    llm_client = client if client is not None else get_shared_async_openai()

    exec_turn_no = 0

    async def _stream_exec_turn(
        merge_overrides: dict[str, Any] | None,
        tools_arg: list[Any],
    ) -> tuple[Any, dict[str, Any], int, Any, str]:
        nonlocal exec_turn_no
        exec_turn_no += 1
        start_ms = time.monotonic_ns() // 1_000_000
        messages = context_manager.get_messages()
        turn_display = exec_turn_no

        if agent_config.debug:
            _logger.debug(
                "LLM 请求 (第 %d 轮): 消息数=%d, 工具数=%d",
                turn_display,
                len(messages),
                len(tools_arg),
            )

        full_content = ""
        full_tool_calls: list[Any] = []
        turn_label = f"[第 {turn_display} 轮]"
        _thinking_started = False
        _tool_call_accum: dict[int, dict[str, str]] = {}
        _usage = None

        if on_thinking and not _thinking_started:
            try:
                await on_thinking(f"{turn_label}", True, turn_label)
                _thinking_started = True
            except Exception:
                pass

        exec_kw = resolve_exec_completion_kwargs(
            agent_config, stream=True, merge_overrides=merge_overrides
        )
        emit_trace({
            "type": "llm.request",
            "phase": "exec",
            "session_key": session_key,
            "turn": turn_display,
            "model": exec_kw["model"],
            "message_count": len(messages),
            "tool_count": len(tools_arg),
        })
        stream = await llm_client.chat.completions.create(
            messages=messages,  # type: ignore[arg-type]
            tools=tools_arg if tools_arg else None,  # type: ignore[arg-type]
            **exec_kw,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if hasattr(chunk, "usage") and chunk.usage:
                _usage = chunk.usage
            if delta.content:
                full_content += delta.content
                if on_thinking:
                    try:
                        await on_thinking(full_content, True, turn_label)
                    except Exception:
                        pass
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in _tool_call_accum:
                        _tool_call_accum[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name if tc_delta.function else "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        _tool_call_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            _tool_call_accum[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            _tool_call_accum[idx]["arguments"] += tc_delta.function.arguments

        if _tool_call_accum:
            full_tool_calls = []
            for idx in sorted(_tool_call_accum.keys()):
                tc_info = _tool_call_accum[idx]
                fn_obj = SimpleNamespace(name=tc_info["name"], arguments=tc_info["arguments"])
                tc_obj = SimpleNamespace(id=tc_info["id"], function=fn_obj)
                full_tool_calls.append(tc_obj)

        msg = SimpleNamespace(
            content=full_content or None,
            tool_calls=full_tool_calls or None,
        )

        if on_thinking and full_tool_calls:
            try:
                for tc in full_tool_calls:
                    try:
                        args_dict = json.loads(tc.function.arguments)
                        intent = _extract_tool_intent(tc.function.name, args_dict)
                    except (json.JSONDecodeError, TypeError):
                        intent = "执行操作"
                    await on_thinking(
                        f"🔧 {tc.function.name} — {intent}", False, turn_label
                    )
            except Exception:
                pass

        emit_trace({
            "type": "llm.response",
            "phase": "exec",
            "session_key": session_key,
            "turn": turn_display,
            "has_tool_calls": bool(full_tool_calls),
            "usage": _usage.model_dump() if _usage else None,
        })

        if agent_config.log_file:
            append_log(agent_config.log_file, {
                "phase": "exec",
                "turn": turn_display,
                "req": {
                    "model": exec_kw["model"],
                    "messageCount": len(messages),
                    "toolCount": len(tools_arg),
                },
                "res": {
                    "hasToolCalls": bool(full_tool_calls),
                    "toolCalls": [{"name": tc.function.name, "args": truncate(tc.function.arguments, 300)}
                                  for tc in full_tool_calls],
                    "content": truncate(full_content or "", 1000) if full_content else None,
                    "usage": _usage.model_dump() if _usage else None,
                },
            })

        al.log_llm_call(
            session_key=session_key,
            turn=turn_display,
            model=exec_kw["model"],
            message_count=len(messages),
            tool_count=len(tools_arg),
            thinking=full_content,
            token_usage=_usage.model_dump() if _usage else None,
        )
        return msg, exec_kw, start_ms, _usage, full_content

    async def _run_tool_calls_phase(msg: Any, start_ms: int) -> str | None:
        nonlocal loop_warning_shown
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        oob_a = _append_context_or_return(context_manager, assistant_msg)
        if oob_a:
            return oob_a

        timeout_sec = max(1, int(agent_config.tool_timeout))
        pending: list[tuple[Any, dict[str, Any], Any]] = []

        for tc in msg.tool_calls:
            tool = effective_registry.get(tc.function.name)
            if tool is None:
                avail = ", ".join(effective_registry.list())
                oob_u = _append_context_or_return(
                    context_manager,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"错误：未知工具 {tc.function.name}。可用: {avail}",
                    },
                )
                if oob_u:
                    return oob_u
                if on_tool_call:
                    on_tool_call(tc.function.name, tc.function.arguments, "⚠️ 未知工具")
                continue

            try:
                args = json.loads(tc.function.arguments)
                loop_check = loop_detector.check(tc.function.name, args)

                if loop_check.level == "critical":
                    elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                    monitor.record(tc.function.name, elapsed, False)
                    _logger.warning("循环检测拦截: %s", loop_check.message)
                    return (
                        f"⚠️ 任务执行被终止：{loop_check.message}\n\n"
                        "建议：简化请求或明确具体目标。"
                    )

                if loop_check.level == "warning" and not loop_warning_shown:
                    loop_warning_shown = True
                    _logger.warning(loop_check.message)
            except Exception:
                args = {}

            pending.append((tc, args, tool))

        async def _run_tool(
            tc: Any, args: dict[str, Any], tool: Any
        ) -> tuple[Any, dict[str, Any], Any, Any, int]:
            from miniagent.types.tool import ToolResult

            tool_start = time.monotonic_ns() // 1_000_000
            emit_trace({
                "type": "tool.start",
                "session_key": session_key,
                "tool": tc.function.name,
            })
            try:
                result = await asyncio.wait_for(
                    tool.handler(args, ctx),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                result = ToolResult(
                    success=False,
                    content=f"⚠️ 工具超时（{timeout_sec}s）: {tc.function.name}",
                )
            except Exception as e:
                result = ToolResult(success=False, content=f"⚠️ 执行异常: {e}")
            tool_elapsed = time.monotonic_ns() // 1_000_000 - tool_start
            emit_trace({
                "type": "tool.end",
                "session_key": session_key,
                "tool": tc.function.name,
                "duration_ms": tool_elapsed,
                "success": result.success,
            })
            return tc, args, tool, result, tool_elapsed

        if pending:
            if agent_config.allow_parallel_tools and len(pending) > 1:
                outcomes = await asyncio.gather(
                    *[_run_tool(tc, args, tool) for tc, args, tool in pending]
                )
            else:
                outcomes = []
                for tc, args, tool in pending:
                    outcomes.append(await _run_tool(tc, args, tool))

            for tc, args, _tool, result, tool_elapsed in outcomes:
                turn_tool_calls.append({
                    "name": tc.function.name,
                    "args": tc.function.arguments,
                    "result": result.content,
                })
                loop_detector.record(tc.function.name, args, result.content)
                monitor.record(tc.function.name, tool_elapsed, result.success)
                oob_t = _append_context_or_return(
                    context_manager,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.content,
                    },
                )
                if oob_t:
                    return oob_t
                intent = _extract_tool_intent(tc.function.name, args)
                al.log_tool_call(
                    session_key=session_key,
                    tool_name=tc.function.name,
                    intent=intent,
                    args=args,
                    result=result.content,
                    duration_ms=tool_elapsed,
                    success=result.success,
                )
        return None

    use_phased = _env_phased_execution_enabled() and bool(plan.steps)

    if not use_phased:
        while turns_left > 0:
            turns_left -= 1
            msg, _exec_kw, start_ms, _usage, _full_content = await _stream_exec_turn(None, tools)

            if not msg.tool_calls:
                final_reply = msg.content or "(空回复)"
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                monitor.record("llm_response", elapsed, True)
                oob = _append_context_or_return(
                    context_manager, {"role": "assistant", "content": final_reply}
                )
                if oob:
                    return oob

                if agent_config.session_key and final_reply:
                    await _save_session_memory(
                        ms,
                        agent_config.session_key,
                        user_input,
                        final_reply,
                        turn_tool_calls,
                    )
                    al.log_final_reply(session_key, final_reply)

                if agent_config.debug:
                    _logger.debug(context_manager.get_token_report())

                return final_reply

            early = await _run_tool_calls_phase(msg, start_ms)
            if early is not None:
                return early
    else:
        n_steps = len(plan.steps)
        for si, step in enumerate(plan.steps):
            is_last = si == n_steps - 1
            step_tools = _resolve_exec_tools(effective_registry, agent_config, plan, step)
            context_manager.set_tools(step_tools)
            step_hint = (
                f"【执行步骤 {step.step_number or si + 1}/{n_steps}】{step.description}\n"
                f"预期输入：{step.expected_input}\n"
                f"预期产出：{step.expected_output}\n"
                "请仅完成本步骤；若当前无需工具，请直接给出简短步骤小结。"
            )
            oob_step = _append_context_or_return(
                context_manager, {"role": "user", "content": step_hint}
            )
            if oob_step:
                return oob_step

            sub_cap = min(_step_max_turns_cap(), turns_left)
            sub_left = sub_cap
            stl, stb = map_business_depth(step.thinking_level)
            step_merge = {"thinking_level": stl, "thinking_budget": stb}

            step_resolved = False
            while sub_left > 0 and turns_left > 0:
                turns_left -= 1
                sub_left -= 1
                msg, _ek, start_ms, _u, _fc = await _stream_exec_turn(step_merge, step_tools)

                if not msg.tool_calls:
                    final_reply = msg.content or "(空回复)"
                    elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                    monitor.record("llm_response", elapsed, True)
                    oob = _append_context_or_return(
                        context_manager, {"role": "assistant", "content": final_reply}
                    )
                    if oob:
                        return oob
                    if is_last and agent_config.session_key and final_reply:
                        await _save_session_memory(
                            ms,
                            agent_config.session_key,
                            user_input,
                            final_reply,
                            turn_tool_calls,
                        )
                        al.log_final_reply(session_key, final_reply)
                    if agent_config.debug:
                        _logger.debug(context_manager.get_token_report())
                    if is_last:
                        return final_reply
                    step_resolved = True
                    break

                early = await _run_tool_calls_phase(msg, start_ms)
                if early is not None:
                    return early

            if is_last and not step_resolved:
                return (
                    "⚠️ 最后一步在单步子轮次（MINIAGENT_STEP_MAX_TURNS）或总轮数限制内，"
                    "未以「无工具调用」形式结束。\n\n"
                    "可提高 MINIAGENT_STEP_MAX_TURNS、AGENT_MAX_TURNS，"
                    "或设置 MINIAGENT_PHASED_EXECUTION=0 退回单循环执行后重试。"
                )

            if not is_last and not step_resolved and turns_left > 0:
                oob_n = _append_context_or_return(
                    context_manager,
                    {
                        "role": "user",
                        "content": (
                            "（系统提示：上一步在单步子轮次内未结束，以下继续下一步；"
                            "若结果不理想可适当提高 MINIAGENT_STEP_MAX_TURNS。）"
                        ),
                    },
                )
                if oob_n:
                    return oob_n

    # ── 达到最大轮数 ──
    loop_stats = loop_detector.get_stats()

    if agent_config.session_key:
        al.log_incomplete(session_key, f"达到最大轮数 {max_turns}")

    if agent_config.debug:
        _logger.debug(context_manager.get_token_report())

    return (
        f"⚠️ 达到最大调用次数（{max_turns} 轮），任务未完成。\n\n"
        f"建议：简化请求，分步骤执行。\n\n"
        f"📊 本轮统计：工具调用 {loop_stats['total_calls']} 次"
    )


# ─── 工具意图提取 ──────────────────────────────────────────

def _extract_tool_intent(tool_name: str, args: dict[str, Any]) -> str:
    """从工具调用中提取简要意图描述。"""
    # 常见工具的意图映射
    intent_map = {
        "read_file": "读取文件",
        "write_file": "写入文件",
        "edit_file": "编辑文件",
        "list_dir": "列出目录",
        "exec_command": "执行命令",
        "web_search": "搜索网页",
        "browser_extract_text": "浏览器提取正文",
        "fetch_url": "抓取网页",
        "read_memory": "读取记忆",
        "write_memory": "写入记忆",
        "search_memory": "搜索记忆",
        "git_status": "Git 状态",
        "git_diff": "Git 差异",
    }
    base_intent = intent_map.get(tool_name, f"调用 {tool_name}")

    # 尝试从参数中提取关键信息
    if args:
        # 优先取 path, query, command, content
        for key in ("path", "query", "command", "content", "url"):
            if key in args:
                val = str(args[key])[:60]
                return f"{base_intent}: {val}"

    return base_intent


# ─── 记忆保存 ────────────────────────────────────────────

async def _save_session_memory(
    memory_store: Any,
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
    await memory_store.add_entry(
        session_key,
        MemoryEntryInput(
            timestamp=now,
            user_snippet=user_input[:100],
            summary=summary,
            facts=facts,
        ),
    )


__all__ = [
    "execute_plan",
    "get_client",
    "MODEL",
    "AGENT_NAME",
    "AGENT_IDENTITY",
    "build_execution_system_prompt",
]
