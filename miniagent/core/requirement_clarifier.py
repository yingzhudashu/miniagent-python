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
    >>> print(clarifier.to_system_prompt(result))  # 注入 system prompt

与 Agent 的集成：
澄清结果可通过 ``to_system_prompt()`` 转为 system prompt 片段注入后续 LLM 调用。
交互模式下，澄清后还需经用户确认才进入规划阶段。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.core.llm_json import llm_json
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


CLARIFY_PROMPT = """你是一个需求分析专家。请按以下步骤分析用户需求：

Step 1 (Wittgenstein - 语言边界)：识别模糊表述、未定义概念、歧义词。
Step 2 (Socrates - 反向追问)：推断隐含约束（专业度、格式、时间、范围）。
Step 3 (Polanyi - 示例传递)：提供正向和反向示例来传递隐性知识。

重要：如果提供了「历史会话记忆」，请先仔细阅读记忆内容，不要重复询问历史中已经回答过的问题。只在记忆确实无法覆盖的模糊点上追问。

请以 JSON 格式返回：
{
  "clarified_goal": "澄清后的目标描述",
  "boundary_conditions": ["约束1", "约束2"],
  "output_spec": "输出规格说明",
  "examples": ["正向示例1"],
  "anti_examples": ["反向示例1"],
  "ambiguity_report": ["模糊点1", "模糊点2"]
}

只返回 JSON，不要其他文字。"""


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
    ) -> ClarifiedRequirement:
        """执行三步需求澄清。

        Args:
            user_input: 用户原始输入
            ask_user: 交互追问回调（接收问题文本，返回用户回答）
            client: LLM 客户端
            on_thinking: 思考过程回调（可选；非 None 时输出澄清进度）
            memory_store: 记忆存储（可选；传入时加载会话记忆注入到澄清 LLM 上下文）
            session_key: 会话标识符（与 memory_store 配合使用）

        Returns:
            澄清后的需求规格
        """
        # 加载会话记忆（让需求分析看到历史上下文）
        memory_context = ""
        if memory_store and session_key:
            try:
                from miniagent.memory.store import format_memory_for_prompt

                memory = await memory_store.load(session_key)
                memory_context = format_memory_for_prompt(memory)
            except Exception:
                pass

        system = CLARIFY_PROMPT
        if memory_context:
            system = f"{memory_context}\n\n{CLARIFY_PROMPT}"

        # Step 1 & 2: LLM 自动推断模糊点和约束
        await invoke_on_thinking(
            on_thinking,
            "正在分析需求，识别模糊表述、边界条件与输出规格…",
            True,
            "[需求澄清]",
        )

        result = await llm_json(
            prompt=user_input,
            system=system,
            client=client,
        )

        clarified = ClarifiedRequirement(
            original=user_input,
            clarified_goal=result.get("clarified_goal", user_input),
            boundary_conditions=result.get("boundary_conditions", []),
            output_spec=result.get("output_spec", ""),
            examples=result.get("examples", []),
            anti_examples=result.get("anti_examples", []),
            ambiguity_report=result.get("ambiguity_report", []),
        )

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

        # 交互模式：针对模糊点追问
        if self.interactive and ask_user and clarified.ambiguity_report:
            for ambiguity in clarified.ambiguity_report[:3]:  # 最多追问 3 个
                answer = await ask_user(f"关于「{ambiguity}」，您能补充说明吗？")
                if answer and answer.strip():
                    clarified.boundary_conditions.append(f"用户补充：{answer.strip()}")

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
        return "\n\n".join(parts)


__all__ = ["RequirementClarifier", "ClarifiedRequirement"]
