"""结果反思评估 — LLM 自评估 Agent 回复质量。

提供 ``reflect_on_result`` 函数，在 Agent 完成回复后调用 LLM 评估结果质量，
判定是否可接受并给出改进建议。由 ``agent.py`` 在每轮结束后调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.core.llm_json import llm_json
from miniagent.core.prompts.reflector import REFLECTOR_PROMPT
from miniagent.core.thinking_callback import invoke_on_thinking

# REFLECTOR_PROMPT_TEMPLATE 用于构建带有用户输入的完整提示词
# REFLECTOR_PROMPT 是优化后的 XML 结构化提示词（不包含用户输入）

# REFLECTION_PROMPT 现在从 miniagent.core.prompts.reflector 导入


@dataclass
class ReflectionResult:
    """LLM 自评估结果。"""

    acceptable: bool            # 结果是否可接受
    quality_score: float        # 0-1 质量评分
    issues: list[str] = field(default_factory=list)       # 发现的问题
    suggestions: list[str] = field(default_factory=list)  # 改进建议


async def reflect_on_result(
    user_input: str,
    reply: str,
    client: AsyncOpenAI | None = None,
    on_thinking: Any | None = None,
) -> ReflectionResult:
    """调用 LLM 评估 Agent 回复质量。

    Args:
        user_input: 用户原始输入
        reply: Agent 执行结果
        client: LLM 客户端
        on_thinking: 思考过程回调

    Returns:
        反思评估结果

    RAG 增强：反思阶段会检索知识库（可选），参考标准评估回答质量。
    """
    if on_thinking:
        await invoke_on_thinking(on_thinking, "评估结果质量...", True, "[反思评估]")

    # ── RAG 增强：知识库检索（使用公共函数）──
    from miniagent.knowledge import retrieve_knowledge_context
    kb_standard = retrieve_knowledge_context(
        user_input, phase="reflector", default_top_k=2, default_max_chars=1500
    )

    # 构建 prompt
    # 构建评估提示：使用优化的 XML 结构化提示词
    prompt = f"用户原始输入：\n{user_input}\n\nAgent 执行结果：\n{reply}"
    if kb_standard:
        prompt = prompt + kb_standard + "\n\n若知识库有更准确的说法，请在 suggestions 中指出。"

    result = await llm_json(
        prompt=prompt,
        system="你是一个结果评估专家。请评估任务完成质量。只返回 JSON。",
        client=client,
    )

    reflection = ReflectionResult(
        acceptable=result.get("acceptable", True),
        quality_score=float(result.get("quality_score", 0.5)),
        issues=result.get("issues", []),
        suggestions=result.get("suggestions", []),
    )

    if on_thinking:
        status = "可接受" if reflection.acceptable else "需改进"
        await invoke_on_thinking(
            on_thinking,
            f"质量评分 {reflection.quality_score:.1f}，判定：{status}",
            True,
            "[反思评估]",
        )

    return reflection


__all__ = ["ReflectionResult", "reflect_on_result", "REFLECTION_PROMPT"]
