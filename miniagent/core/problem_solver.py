"""结果反思评估 — LLM 自评估 Agent 回复质量。

提供 ``reflect_on_result`` 函数，在 Agent 完成回复后调用 LLM 评估结果质量，
判定是否可接受并给出改进建议。由 ``agent.py`` 在每轮结束后调用。
"""

from __future__ import annotations

import re
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

    # 使用优化后的 XML 结构化提示词
    result = await llm_json(
        prompt=prompt,
        system=REFLECTOR_PROMPT,
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


# ─── 质量评估尾部（footer）：构建与剥离 ──────────────────────────
#
# footer 是**展示层**内容，附加在回复末尾给用户看（CLI 终端 / 飞书卡片）。
# 它**不应进入回灌给 LLM 的会话历史**：否则下一轮 LLM 会把上一轮的 footer
# 当作回复正文的一部分复述出来，叠加本轮 Phase 3 新生成的 footer，造成
# 「同一答案出现两次质量评估」且随历史累积自我强化。
#
# 因此 footer 的**生成格式**与**剥离正则**集中在此，保证二者同步、不漂移：
# - ``build_reflection_footer`` 由 ``agent.py`` 调用，拼到对外展示的 reply。
# - ``strip_reflection_footer`` 由 ``history_bridge`` 在构造 LLM 上下文时调用，
#   把 assistant 历史中的 footer 剥掉（同时清理已被污染的旧历史）。

_REFLECTION_FOOTER_SEP = "\n\n---\n🤖 "

# 匹配整段 footer：分隔符 + 状态/评分行 +（可选）建议块，直到字符串结尾。
# 使用 DOTALL 让建议块的多行内容被一并吞掉；锚定到结尾避免误伤正文。
_REFLECTION_FOOTER_PATTERN = re.compile(
    r"\n\n---\n🤖 (?:质量评估通过|质量评估需改进) \| 质量评分 \d(?:\.\d)?"
    r"(?:\n\n建议：\n(?:- .*(?:\n|$))+)?\s*$"
)


def build_reflection_footer(reflection: ReflectionResult) -> str:
    """根据反思结果构建展示用的质量评估尾部文本（含前导分隔符）。"""
    status = "质量评估通过" if reflection.acceptable else "质量评估需改进"
    footer = f"{_REFLECTION_FOOTER_SEP}{status} | 质量评分 {reflection.quality_score:.1f}"
    if reflection.suggestions:
        footer += "\n\n建议：\n" + "\n".join(f"- {s}" for s in reflection.suggestions[:5])
    return footer


def strip_reflection_footer(content: str) -> str:
    """从回复文本末尾剥离质量评估尾部（若存在）。

    用于把 assistant 历史回灌给 LLM 前清除 footer，避免被模型复述导致
    重复质量评估。对不含 footer 的文本原样返回。

    已被污染的旧历史可能在末尾累积**多个**相邻 footer（模型复述 + 真实生成），
    故循环剥离直到稳定。
    """
    if not content or _REFLECTION_FOOTER_SEP not in content:
        return content
    prev = content
    while True:
        stripped = _REFLECTION_FOOTER_PATTERN.sub("", prev).rstrip()
        if stripped == prev:
            return stripped
        prev = stripped


__all__ = [
    "ReflectionResult",
    "reflect_on_result",
    "REFLECTOR_PROMPT",
    "build_reflection_footer",
    "strip_reflection_footer",
]
