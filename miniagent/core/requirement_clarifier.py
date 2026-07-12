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
- **自动推断**（``interactive=False``）：LLM 一次性分析，零交互
- **交互追问**（``interactive=True`` 且提供 ``ask_user``）：针对高影响未解模糊点实时追问
  追问前会先加载历史记忆与知识库，避免重复提问。

使用方式：
    >>> clarifier = RequirementClarifier(interactive=True)
    >>> result = await clarifier.clarify("帮我查一下天气", ask_user=..., memory_store=..., session_key="...")
    >>> print(result.clarified_goal)  # "获取指定城市的天气预报"
    >>> print(clarifier.to_system_prompt(result))  # 生成后续阶段可拼接的澄清片段

与 Agent 的集成：
澄清结果可通过 ``to_system_prompt()`` 转为结构化提示片段，供后续规划/执行阶段拼接。
交互模式下，逐条追问经 ``ConfirmationStage.CLARIFICATION`` 等待用户回复；澄清完成后
直接进入规划阶段（无单独的「澄清结果整体验证」步骤）。
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

from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
from miniagent.core.llm_json import llm_json
from miniagent.core.prompts.clarifier import CLARIFIER_PROMPT
from miniagent.core.thinking_callback import invoke_on_thinking


@dataclass
class ClarifiedRequirement:
    """澄清后的需求规格。

    LLM 直接产出 ``clarified_goal``、``boundary_conditions``、``output_spec``、
    ``examples``、``anti_examples``、``ambiguity_report``。
    ``memory_resolved_facts`` 等消解字段由 Ground Truth 后处理填充，不由 LLM 返回。

    Attributes:
        clarification_needed: 澄清流程结束后是否仍有未解问题（含因 ``max_questions``
            上限未追问、或用户未回答而遗留的项）。
    """

    original: str
    clarified_goal: str = ""
    boundary_conditions: list[str] = field(default_factory=list)
    output_spec: str = ""
    examples: list[str] = field(default_factory=list)
    anti_examples: list[str] = field(default_factory=list)
    ambiguity_report: list[str] = field(default_factory=list)
    resolved_assumptions: list[str] = field(default_factory=list)
    memory_resolved_facts: list[str] = field(default_factory=list)
    knowledge_resolved_facts: list[str] = field(default_factory=list)
    default_resolved_assumptions: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    clarification_needed: bool = False


@dataclass
class RequirementClarifier:
    """三步需求澄清器。

    Step 1 (Wittgenstein)：语言边界 — 识别模糊表述、未定义概念
    Step 2 (Socrates)：反向追问 — 推断隐含约束（专业度/格式/时间）
    Step 3 (Polanyi)：示例传递 — 注入正反向示例到上下文

    支持两种模式：
    - 交互追问：``interactive=True`` 且提供 ``ask_user`` 回调时，实时向用户追问
    - 自动推断：``interactive=False`` 或未提供 ``ask_user``，仅靠 LLM 与 Ground Truth 推断

    Args:
        interactive: 是否启用交互追问（还需 ``ask_user`` 回调才会实际提问）
    """

    interactive: bool = False

    async def clarify(
        self,
        user_input: str,
        *,
        knowledge_registry: KnowledgeRegistryProtocol,
        client: AsyncOpenAI,
        ask_user: Callable[[str], Awaitable[str]] | None = None,
        on_thinking: Any | None = None,
        memory_store: Any | None = None,
        session_key: str | None = None,
        max_questions: int = 3,
    ) -> ClarifiedRequirement:
        """执行三步需求澄清。

        Args:
            user_input: 用户原始输入
            knowledge_registry: 由组合根注入的知识库注册表
            ask_user: 交互追问回调（接收问题文本，返回用户回答）
            client: LLM 客户端
            on_thinking: 思考过程回调；澄清摘要经此输出（``agent.run_agent`` 会传入）
            memory_store: 记忆存储（可选；传入时加载会话记忆注入到澄清 LLM 上下文）
            session_key: 会话标识符（与 memory_store 配合使用）
            max_questions: 交互模式下最多追问数量

        Returns:
            澄清后的需求规格

        Raises:
            json.JSONDecodeError: LLM 返回无法解析的 JSON（``raise_on_error=True``）
            TypeError: LLM 返回非 JSON 对象
            Exception: LLM API 调用失败

        Note:
            澄清阶段会检索知识库（RAG），避免询问知识库已有答案的问题。
            ``ambiguity_report`` 经 Ground Truth 规则消解后写入各 ``*_resolved_*`` 字段。
        """
        start_time = time.monotonic_ns()
        llm_success = True

        memory_context = ""
        memory = None
        if memory_store and session_key:
            try:
                from miniagent.memory.store import format_memory_for_prompt

                memory = await memory_store.load(session_key)
                memory_context = format_memory_for_prompt(memory)
            except Exception as e:
                _logger.debug("加载记忆失败: %s", e)

        from miniagent.knowledge import retrieve_knowledge_context

        kb_context = retrieve_knowledge_context(
            knowledge_registry,
            user_input,
            phase="clarifier",
            default_top_k=3,
            default_max_chars=3000,
        )

        context_parts: list[str] = []
        if memory_context:
            context_parts.append(memory_context)
        if kb_context:
            context_parts.append(kb_context)
        full_context = "\n\n".join(context_parts) if context_parts else ""

        system = CLARIFIER_PROMPT
        if full_context:
            system = f"{full_context}\n\n{CLARIFIER_PROMPT}"

        result = await llm_json(
            prompt=user_input,
            system=system,
            client=client,
            trace_phase="clarify",
            trace_session_key=session_key,
            raise_on_error=True,
        )

        if not result:
            _logger.warning("需求澄清 LLM 返回空结果，使用原始输入")
            llm_success = False
            clarified = ClarifiedRequirement(
                original=user_input,
                clarified_goal=user_input,
            )
            await self._emit_clarify_trace(
                session_key=session_key,
                start_time=start_time,
                clarified=clarified,
                asked_count=0,
                success=False,
            )
            return clarified

        clarified = ClarifiedRequirement(
            original=user_input,
            clarified_goal=result.get("clarified_goal") or user_input,
            boundary_conditions=list(result.get("boundary_conditions") or []),
            output_spec=str(result.get("output_spec") or ""),
            examples=list(result.get("examples") or []),
            anti_examples=list(result.get("anti_examples") or []),
            ambiguity_report=list(result.get("ambiguity_report") or []),
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
            clarified.resolved_assumptions.extend(
                memory_resolved + knowledge_resolved + default_resolved
            )
            clarified.unresolved_questions.extend(
                prioritize_clarification_questions(unresolved)
            )

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

        clarified.clarification_needed = bool(clarified.unresolved_questions)

        await self._emit_clarify_trace(
            session_key=session_key,
            start_time=start_time,
            clarified=clarified,
            asked_count=asked_count,
            success=llm_success,
        )
        return clarified

    async def _emit_clarify_trace(
        self,
        *,
        session_key: str | None,
        start_time: int,
        clarified: ClarifiedRequirement,
        asked_count: int,
        success: bool,
    ) -> None:
        try:
            from miniagent.infrastructure.trace_events import EVENT_REQUIREMENT_CLARIFY
            from miniagent.infrastructure.tracing import emit_trace

            emit_trace({
                "type": EVENT_REQUIREMENT_CLARIFY,
                "session_key": session_key or "",
                "duration_ms": (time.monotonic_ns() - start_time) // 1_000_000,
                "success": success,
                "ambiguity_count": len(clarified.ambiguity_report),
                "asked_count": asked_count,
                "memory_resolved_count": len(clarified.memory_resolved_facts),
                "knowledge_resolved_count": len(clarified.knowledge_resolved_facts),
                "default_resolved_count": len(clarified.default_resolved_assumptions),
                "unresolved_count": len(clarified.unresolved_questions),
            })
        except Exception as e:
            _logger.debug("需求澄清 trace 发送失败: %s", e)

    def to_system_prompt(self, clarified: ClarifiedRequirement) -> str:
        """将澄清结果转为 prompt 片段，供规划/执行阶段拼接到用户输入。

        包含：目标、约束、输出规格、正/反示例、各类消解依据、未解问题。
        不包含 ``ambiguity_report``（内部诊断）与 ``resolved_assumptions``
        （与各 ``*_resolved_*`` 字段重复）。
        """
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
