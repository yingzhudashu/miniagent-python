"""结果反思评估 — LLM 自评估 Agent 回复质量。

提供 ``reflect_on_result`` 在 Agent 完成回复后调用 LLM 评估结果质量；
``build_reflection_footer`` / ``strip_reflection_footer`` 负责展示层尾部
的生成与从历史中剥离（由 ``agent.py`` 与 ``history_bridge`` 分别调用）。
系统提示词 ``REFLECTOR_PROMPT`` 定义于 ``miniagent.core.prompts.reflector``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
from miniagent.core.llm_json import llm_json
from miniagent.core.prompts.reflector import REFLECTOR_PROMPT
from miniagent.core.thinking_callback import invoke_on_thinking

_FOOTER_ITEM_LIMIT = 5


@dataclass
class ReflectionResult:
    """LLM 自评估结果。

    Attributes:
        acceptable: 结果是否可接受。
        quality_score: 0.0–1.0 质量评分。
        issues: 发现的具体问题（展示层最多 ``_FOOTER_ITEM_LIMIT`` 条）。
        suggestions: 可操作的改进建议（展示层最多 ``_FOOTER_ITEM_LIMIT`` 条）。
    """

    acceptable: bool
    quality_score: float
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


def _coerce_bool(value: Any, *, default: bool = True) -> bool:
    """将 LLM 返回值归一化为 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    return default


def _coerce_quality_score(value: Any, *, default: float = 0.5) -> float:
    """将 LLM 返回值归一化为 [0.0, 1.0] 区间的 float。"""
    if value is None:
        return default
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score != score:  # NaN
        return default
    return max(0.0, min(1.0, score))


def _coerce_str_list(value: Any) -> list[str]:
    """将 LLM 返回值归一化为非空字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _parse_reflection_result(result: dict[str, Any]) -> ReflectionResult:
    """将 ``llm_json`` 返回的字典解析为 ``ReflectionResult``。"""
    return ReflectionResult(
        acceptable=_coerce_bool(result.get("acceptable"), default=True),
        quality_score=_coerce_quality_score(result.get("quality_score")),
        issues=_coerce_str_list(result.get("issues")),
        suggestions=_coerce_str_list(result.get("suggestions")),
    )


def _sanitize_footer_line(text: str) -> str:
    """压缩 footer 条目的空白与换行，保证每条建议/问题占单行。"""
    return " ".join(text.split())


def _format_footer_bullets(label: str, items: list[str]) -> str:
    """为 footer 追加「标签 + 列表」块；无条目时返回空字符串。"""
    if not items:
        return ""
    lines = [f"- {_sanitize_footer_line(item)}" for item in items[:_FOOTER_ITEM_LIMIT]]
    return f"\n\n{label}：\n" + "\n".join(lines)


async def reflect_on_result(
    user_input: str,
    reply: str,
    client: AsyncOpenAI,
    on_thinking: Any | None = None,
    *,
    knowledge_registry: KnowledgeRegistryProtocol,
    session_key: str | None = None,
) -> ReflectionResult:
    """调用 LLM 评估 Agent 回复质量。

    Args:
        user_input: 用户原始输入。
        reply: Agent 执行结果。
        client: LLM 客户端。
        on_thinking: 思考过程回调。
        knowledge_registry: 由组合根注入的知识库注册表。
        session_key: 会话标识（用于 trace 归属）。

    Returns:
        反思评估结果。字段经 :func:`_parse_reflection_result` 归一化；
        当 ``llm_json`` 返回空字典（解析失败且 ``raise_on_error=False``）时，
        使用 ``acceptable=True``、``quality_score=0.5`` 及空列表作为默认值。

    RAG 增强：反思阶段会检索知识库（可选），参考标准评估回答质量。

    性能：反思是结构化 JSON 评分，不需要深度思考与大输出。默认对其施加
    bounded thinking（low）与 max_tokens 上限（``features.reflection_max_tokens``，默认 512），
    实测可降低单次延迟约 1/3，且不改变可接受性判定与评分语义。

    Raises:
        Exception: 底层 LLM API 调用失败时原样抛出（网络错误、鉴权失败等）。
    """
    from miniagent.infrastructure.json_config import get_config

    if on_thinking:
        await invoke_on_thinking(on_thinking, "评估结果质量...", True, "[反思评估]")

    from miniagent.knowledge import retrieve_knowledge_context

    kb_standard = retrieve_knowledge_context(
        knowledge_registry,
        user_input,
        phase="reflector",
        default_top_k=2,
        default_max_chars=1500,
    )

    prompt = f"用户原始输入：\n{user_input}\n\nAgent 执行结果：\n{reply}"
    if kb_standard:
        prompt = prompt + kb_standard + "\n\n若知识库有更准确的说法，请在 suggestions 中指出。"

    reflect_max_tokens = int(get_config("features.reflection_max_tokens", 512))

    result = await llm_json(
        prompt=prompt,
        system=REFLECTOR_PROMPT,
        client=client,
        max_tokens=reflect_max_tokens,
        thinking_level="low",
        thinking_budget=0,
        trace_phase="reflect",
        trace_session_key=session_key,
    )

    reflection = _parse_reflection_result(result)

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
# - 每条问题/建议经 ``_sanitize_footer_line`` 压成单行，避免多行条目导致正则剥离失败。

_REFLECTION_FOOTER_SEP = "\n\n---\n🤖 "

# 匹配整段 footer：固定 header + 余下内容直到字符串结尾。
# footer 始终附加在回复末尾，header 具有足够辨识度，锚定 $ 即可避免误伤正文。
_REFLECTION_FOOTER_PATTERN = re.compile(
    r"\n\n---\n🤖 (?:质量评估通过|质量评估需改进) \| 质量评分 \d(?:\.\d)?[\s\S]*$"
)


def build_reflection_footer(reflection: ReflectionResult) -> str:
    """根据反思结果构建展示用的质量评估尾部文本（含前导分隔符）。

    展示 ``issues`` 与 ``suggestions``，各最多 :data:`_FOOTER_ITEM_LIMIT` 条；
    条目内换行会被压成空格以保证与 :func:`strip_reflection_footer` 往返一致。
    """
    status = "质量评估通过" if reflection.acceptable else "质量评估需改进"
    footer = f"{_REFLECTION_FOOTER_SEP}{status} | 质量评分 {reflection.quality_score:.1f}"
    footer += _format_footer_bullets("问题", reflection.issues)
    footer += _format_footer_bullets("建议", reflection.suggestions)
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
