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
from miniagent.core.thinking_callback import invoke_on_thinking

REFLECTION_PROMPT = """你是一个结果评估专家。请评估以下任务的完成质量。

用户原始输入：
{user_input}

Agent 执行结果：
{reply}

评估要求：
1. 如果结果不可接受（acceptable=false），必须给出至少 3 条具体、可操作的改进建议
2. 如果结果可接受但有不足（quality_score<0.8），也应给出改进建议
3. 改进建议应具体指出哪里可以做得更好、更准确、更完整
4. 建议条数最多 5 条

请以 JSON 格式返回评估：
{{
  "acceptable": true/false,
  "quality_score": 0.0-1.0,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"]
}}

只返回 JSON，不要其他文字。"""


@dataclass
class ReflectionResult:
    """LLM 自评估结果质量。"""

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
    """
    if on_thinking:
        await invoke_on_thinking(on_thinking, "评估结果质量...", True, "[反思评估]")

    result = await llm_json(
        prompt=REFLECTION_PROMPT.format(user_input=user_input, reply=reply),
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
