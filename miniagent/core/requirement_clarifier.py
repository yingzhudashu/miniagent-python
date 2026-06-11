"""Requirement Clarifier — 三步需求澄清器。

本模块在规划之前执行**三步需求澄清**，将用户模糊输入转化为结构化需求规格，
供后续规划器和执行器使用。

三步流程：
1. **Wittgenstein（语言边界）**：识别模糊表述、未定义概念、歧义词
   > "语言的边界就是世界的边界" — 明确什么是可表达的、什么是不可表达的
2. **Socrates（反向追问）**：推断隐含约束（专业度、格式、时间、范围）
   > 通过反向追问暴露隐式边界条件，直到无法再言语回答为止
3. **Polanyi（示例传递）**：提供正反向示例来传递隐性知识
   > "我们知道的比我们能说出的更多" — 用示例而非规则传递 tacit knowledge

两种模式：
- **自动推断**：LLM 一次性分析，零交互
- **交互追问**：针对 LLM 识别的模糊点实时向用户追问（需 ``ask_user`` 回调）
  追问前会先加载历史记忆，避免重复提问。

使用方式：
    >>> clarifier = RequirementClarifier(interactive=True)
    >>> result = await clarifier.clarify("帮我查一下天气", ask_user=..., memory_store=..., session_key="...")
    >>> print(result.clarified_goal)  # "获取指定城市的天气预报"
    >>> print(clarifier.to_system_prompt(result))  # 生成后续阶段可拼接的澄清片段

与 Agent 的集成：
澄清结果可通过 ``to_system_prompt()`` 转为结构化提示片段，供后续规划/执行阶段拼接。
交互模式下，澄清后还需经用户确认才进入规划阶段。
"""

from __future__ import annotations

import logging
import time

_logger = logging.getLogger(__name__)

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.core.llm_json import llm_json
from miniagent.core.prompts.clarifier import CLARIFIER_PROMPT
from miniagent.core.thinking_callback import invoke_on_thinking


@dataclass
class ClarifiedRequirement:
    """澄清后的需求规格。"""

    original: str                           # 用户原始输入
    clarified_goal: str = ""                # 澄清后的目标
    boundary_conditions: list[str] = field(default_factory=list)  # 边界条件
    output_spec: str = ""                   # 输出规格（格式、长度、专业度）
    examples: list[str] = field(default_factory=list)     # 正向示例
    anti_examples: list[str] = field(default_factory=list) # 反向示例
    ambiguity_report: list[str] = field(default_factory=list)  # 模糊表述列表
    resolved_assumptions: list[str] = field(default_factory=list)  # 已通过上下文或默认值自澄清
    memory_resolved_facts: list[str] = field(default_factory=list)  # 由长期记忆解答的模糊点
    knowledge_resolved_facts: list[str] = field(default_factory=list)  # 由知识库解答的模糊点
    default_resolved_assumptions: list[str] = field(default_factory=list)  # 安全默认值
    unresolved_questions: list[str] = field(default_factory=list)  # 仍需用户或后续阶段注意的问题
    clarification_needed: bool = False  # 本次是否实际需要向用户追问


# CLARIFY_PROMPT 现在从 miniagent.core.prompts.clarifier 导入


@dataclass
class RequirementClarifier:
    """三步需求澄清器。

    Step 1 (Wittgenstein)：语言边界 — 识别模糊表述、未定义概念
    Step 2 (Socrates)：反向追问 — 推断隐含约束（专业度/格式/时间）
    Step 3 (Polanyi)：示例传递 — 注入正反向示例到上下文

    支持两种模式：
    - 交互追问：提供 ask_user 回调时，实时向用户追问
    - 自动推断：无 ask_user 回调，仅靠 LLM 推断

    Args:
        interactive: 是否启用交互模式（需 ask_user 回调）
    """

    interactive: bool = False

    async def clarify(
        self,
        user_input: str,
        *,
        ask_user: Callable[[str], Awaitable[str]] | None = None,
        client: AsyncOpenAI | None = None,
        on_thinking: Any | None = None,
        memory_store: Any | None = None,
        session_key: str | None = None,
        max_questions: int = 3,
    ) -> ClarifiedRequirement:
        """执行三步需求澄清。

        Args:
            user_input: 用户原始输入
            ask_user: 交互追问回调（接收问题文本，返回用户回答）
            client: LLM 客户端
            on_thinking: 思考过程回调（可选；非 None 时输出澄清进度）
            memory_store: 记忆存储（可选；传入时加载会话记忆注入到澄清 LLM 上下文）
            session_key: 会话标识符（与 memory_store 配合使用）
            max_questions: 最多追问数量

        Returns:
            澄清后的需求规格

        RAG 增强：澄清阶段会检索知识库（可选），避免询问知识库已有答案的问题。
        """
        start_time = time.monotonic_ns()

        # 加载会话记忆（让需求分析看到历史上下文）
        memory_context = ""
        memory = None
        if memory_store and session_key:
            try:
                from miniagent.memory.store import format_memory_for_prompt

                memory = await memory_store.load(session_key)
                memory_context = format_memory_for_prompt(memory)
            except Exception as e:
                _logger.debug("加载记忆失败: %s", e)

        # ── RAG 增强：知识库检索（使用公共函数）──
        from miniagent.knowledge import retrieve_knowledge_context
        kb_context = retrieve_knowledge_context(
            user_input, phase="clarifier", default_top_k=3, default_max_chars=3000
        )

        # 合并上下文：记忆 + 知识库
        context_parts = []
        if memory_context:
            context_parts.append(memory_context)
        if kb_context:
            context_parts.append(kb_context)
        full_context = "\n\n".join(context_parts) if context_parts else ""

        system = CLARIFIER_PROMPT
        if full_context:
            system = f"{full_context}\n\n{CLARIFIER_PROMPT}"

        # agent.py 已在 LLM 调用前发送"正在分析需求…"提示，此处不再重复发送。

        result = await llm_json(
            prompt=user_input,
            system=system,
            client=client,
            trace_phase="clarify",
            trace_session_key=session_key,
        )

        clarified = ClarifiedRequirement(
            original=user_input,
            clarified_goal=result.get("clarified_goal", user_input),
            boundary_conditions=result.get("boundary_conditions", []),
            output_spec=result.get("output_spec", ""),
            examples=result.get("examples", []),
            anti_examples=result.get("anti_examples", []),
            ambiguity_report=result.get("ambiguity_report", []),
            resolved_assumptions=result.get("resolved_assumptions", []),
            memory_resolved_facts=result.get("memory_resolved_facts", []),
            knowledge_resolved_facts=result.get("knowledge_resolved_facts", []),
            default_resolved_assumptions=result.get("default_resolved_assumptions", []),
            unresolved_questions=result.get("unresolved_questions", []),
            clarification_needed=bool(result.get("clarification_needed", False)),
        )

        if clarified.ambiguity_report:
            from miniagent.memory.ground_truth import (
                prioritize_clarification_questions,
                resolve_ambiguities_from_ground_truth,
            )

            (
                memory_resolved,
                knowledge_resolved,
                default_resolved,
                unresolved,
            ) = resolve_ambiguities_from_ground_truth(
                clarified.ambiguity_report,
                memory,
                knowledge_context=kb_context,
                user_input=user_input,
            )
            clarified.memory_resolved_facts.extend(memory_resolved)
            clarified.knowledge_resolved_facts.extend(knowledge_resolved)
            clarified.default_resolved_assumptions.extend(default_resolved)
            clarified.resolved_assumptions.extend(memory_resolved + knowledge_resolved + default_resolved)
            clarified.unresolved_questions.extend(prioritize_clarification_questions(unresolved))
            clarified.clarification_needed = bool(clarified.unresolved_questions)

        # 输出澄清结果摘要
        parts: list[str] = []
        if clarified.clarified_goal and clarified.clarified_goal != user_input:
            parts.append(f"目标：{clarified.clarified_goal}")
        if clarified.boundary_conditions:
            parts.append(f"约束：{'、'.join(clarified.boundary_conditions[:5])}")
        if clarified.output_spec:
            parts.append(f"输出规格：{clarified.output_spec}")
        summary = "；".join(parts) if parts else "未识别额外约束"
        await invoke_on_thinking(
            on_thinking,
            summary,
            True,
            "[需求澄清]",
        )

        asked_count = 0
        # 交互模式：只针对无法由记忆/知识库/安全默认值解答的高影响问题追问。
        if self.interactive and ask_user and clarified.unresolved_questions:
            questions_to_ask = clarified.unresolved_questions[: max(0, max_questions)]
            remaining_unasked = clarified.unresolved_questions[len(questions_to_ask):]
            clarified.unresolved_questions = list(remaining_unasked)
            for ambiguity in questions_to_ask:
                answer = await ask_user(f"关于「{ambiguity}」，您能补充说明吗？")
                asked_count += 1
                if answer and answer.strip():
                    clarified.boundary_conditions.append(f"用户补充：{answer.strip()}")
                else:
                    clarified.unresolved_questions.append(ambiguity)
            clarified.clarification_needed = asked_count > 0

        try:
            from miniagent.infrastructure.trace_events import EVENT_REQUIREMENT_CLARIFY
            from miniagent.infrastructure.tracing import emit_trace

            emit_trace({
                "type": EVENT_REQUIREMENT_CLARIFY,
                "session_key": session_key or "",
                "duration_ms": (time.monotonic_ns() - start_time) // 1_000_000,
                "success": True,
                "ambiguity_count": len(clarified.ambiguity_report),
                "asked_count": asked_count,
                "memory_resolved_count": len(clarified.memory_resolved_facts),
                "knowledge_resolved_count": len(clarified.knowledge_resolved_facts),
                "default_resolved_count": len(clarified.default_resolved_assumptions),
                "unresolved_count": len(clarified.unresolved_questions),
            })
        except Exception as e:
            _logger.debug("需求澄清 trace 发送失败: %s", e)

        return clarified

    def to_system_prompt(self, clarified: ClarifiedRequirement) -> str:
        """将澄清结果转为 system prompt 片段，注入到后续 LLM 调用。"""
        parts: list[str] = [
            "## 需求规格",
            f"目标：{clarified.clarified_goal}",
        ]
        if clarified.boundary_conditions:
            parts.append("约束：\n" + "\n".join(f"- {c}" for c in clarified.boundary_conditions))
        if clarified.output_spec:
            parts.append(f"输出规格：{clarified.output_spec}")
        if clarified.examples:
            parts.append("正向示例：\n" + "\n".join(f"- {e}" for e in clarified.examples))
        if clarified.anti_examples:
            parts.append("避免以下：\n" + "\n".join(f"- {e}" for e in clarified.anti_examples))
        if clarified.memory_resolved_facts:
            parts.append("记忆已解答：\n" + "\n".join(f"- {f}" for f in clarified.memory_resolved_facts))
        if clarified.knowledge_resolved_facts:
            parts.append("知识库已解答：\n" + "\n".join(f"- {f}" for f in clarified.knowledge_resolved_facts))
        if clarified.default_resolved_assumptions:
            parts.append("默认假设：\n" + "\n".join(f"- {f}" for f in clarified.default_resolved_assumptions))
        if clarified.unresolved_questions:
            parts.append("仍需注意的未解问题：\n" + "\n".join(f"- {q}" for q in clarified.unresolved_questions))
        return "\n\n".join(parts)


__all__ = ["RequirementClarifier", "ClarifiedRequirement"]
