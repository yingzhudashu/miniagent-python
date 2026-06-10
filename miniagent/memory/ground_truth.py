"""Solid ground truth helpers for long-term memory.

The functions in this module are deterministic and intentionally conservative:
they only promote stable user preferences, workflow constraints, environment
facts, and output-format choices into ``ground_truth_facts``. One-off task
details stay in normal memory entries so they do not become stale defaults.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from miniagent.types.memory import GroundTruthFact, SessionMemory

ACTIVE_STATUS = "active"
SUPERSEDED_STATUS = "superseded"

_FACT_LIMIT = 200
_TEXT_LIMIT = 2000
_MATCH_THRESHOLD = 0.22

_CORRECTION_PREFIX_RE = re.compile(
    r"(?:纠正一下|更正一下|之前(?:说|记)的不对|不是(?P<old>[^，。；;]+)(?:，|,)?而是(?P<new>[^。；;]+)|"
    r"我说的是(?P<said>[^。；;]+)|以后(?:改成|默认|都)?(?P<future>[^。；;]+))"
)

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "output_format",
        re.compile(
            r"(?:以后|默认|记住)?(?:回复|回答|输出|文档|结果)(?:都|默认)?(?:用|使用|采用|要)?"
            r"(?P<value>中文|英文|Markdown|表格|JSON|代码块|简洁|详细)[^。；;\n]*"
        ),
    ),
    (
        "workflow_preference",
        re.compile(
            r"(?:以后|默认|记住)?(?:先|优先|不要|避免|必须|总是|每次)"
            r"(?P<value>[^。；;\n]{2,120})"
        ),
    ),
    (
        "project_constraint",
        re.compile(
            r"(?:项目|仓库|代码库|当前项目)(?:要求|约束|默认|必须|不要|使用)"
            r"(?P<value>[^。；;\n]{2,120})"
        ),
    ),
    (
        "environment",
        re.compile(
            r"(?:环境|系统|shell|终端|工作区|路径|时区|语言)(?:是|为|默认|使用)?"
            r"(?P<value>[^。；;\n]{2,120})"
        ),
    ),
    (
        "preference",
        re.compile(
            r"(?:记住|偏好是|我(?:希望|喜欢|偏好)|以后(?:都|默认)?|默认(?:是|用|使用)?)"
            r"(?P<value>[^。；;\n]{2,120})"
        ),
    ),
)

_STOPWORDS = {
    "关于",
    "能",
    "补充",
    "说明",
    "吗",
    "是否",
    "需要",
    "如何",
    "什么",
    "哪个",
    "哪些",
    "用户",
    "任务",
    "需求",
    "输出",
    "格式",
}


def utc_now() -> str:
    """Return a stable ISO timestamp for persisted fact metadata."""
    return datetime.now(timezone.utc).isoformat()


def normalize_fact_key(value: str, category: str) -> str:
    """Build a coarse key so later corrections update the same stable fact."""
    text = _compact(value).lower()
    if any(term in text for term in ("回复", "回答", "输出")) and any(term in text for term in ("中文", "英文")):
        return "output.language"
    if any(term in text for term in ("回复", "回答", "输出")) and "markdown" in text:
        return "output.format.markdown"
    if category == "output_format":
        if "markdown" in text:
            return "output.format.markdown"
        if "json" in text:
            return "output.format.json"
        if "表格" in text:
            return "output.format.table"
        if "英文" in text or "中文" in text:
            return "output.language"
        if "详细" in text or "简洁" in text:
            return "output.detail"
    if category == "environment":
        for name in ("shell", "powershell", "终端", "时区", "路径", "工作区", "系统"):
            if name in text:
                return f"environment.{name}"
    if category == "project_constraint":
        return "project.constraint"
    if category == "workflow_preference":
        return "workflow.preference"
    return "preference.general"


def ground_truth_to_text(fact: GroundTruthFact) -> str:
    """Format one fact for memory prompts without leaking internal metadata."""
    return f"{fact.key}: {fact.value}"


def active_ground_truth(memory: SessionMemory | None) -> list[GroundTruthFact]:
    """Return active, high-confidence facts ordered as stored."""
    if not memory:
        return []
    return [
        fact
        for fact in memory.ground_truth_facts
        if fact.status == ACTIVE_STATUS and fact.confidence >= 0.75 and fact.key and fact.value
    ]


def format_ground_truth_for_prompt(memory: SessionMemory | None, *, limit: int = 10) -> list[str]:
    """Return active facts formatted for prompt injection."""
    return [ground_truth_to_text(fact) for fact in active_ground_truth(memory)[-limit:]]


def extract_ground_truth_facts(text: str, *, source: str = "user", now: str | None = None) -> list[GroundTruthFact]:
    """Extract stable facts from user-visible text using conservative patterns."""
    if not text:
        return []

    timestamp = now or utc_now()
    facts: list[GroundTruthFact] = []
    seen: set[tuple[str, str]] = set()
    clipped = text[:_TEXT_LIMIT]
    for category, pattern in _CATEGORY_PATTERNS:
        for match in pattern.finditer(clipped):
            raw_value = _compact(match.group("value"))
            value = _clean_value(raw_value)
            if not _is_stable_value(value):
                continue
            key = normalize_fact_key(value, category)
            dedupe = (key, value.lower())
            if dedupe in seen:
                continue
            seen.add(dedupe)
            facts.append(
                GroundTruthFact(
                    key=key,
                    value=value[:_FACT_LIMIT],
                    category=category,
                    confidence=0.95,
                    source=source,
                    status=ACTIVE_STATUS,
                    created_at=timestamp,
                    updated_at=timestamp,
                    evidence=_compact(text)[:_FACT_LIMIT],
                )
            )
    return facts


def upsert_ground_truth_facts(
    memory: SessionMemory,
    candidates: list[GroundTruthFact],
    *,
    now: str | None = None,
) -> int:
    """Merge extracted facts into memory and supersede stale active values."""
    if not candidates:
        return 0

    timestamp = now or utc_now()
    changed = 0
    for candidate in candidates:
        candidate.updated_at = candidate.updated_at or timestamp
        candidate.created_at = candidate.created_at or timestamp
        existing_active = _find_active_by_key(memory.ground_truth_facts, candidate.key)
        if existing_active is None:
            memory.ground_truth_facts.append(candidate)
            changed += 1
            continue
        if _same_value(existing_active.value, candidate.value):
            existing_active.updated_at = timestamp
            existing_active.confidence = max(existing_active.confidence, candidate.confidence)
            if candidate.evidence:
                existing_active.evidence = candidate.evidence
            continue

        existing_active.status = SUPERSEDED_STATUS
        existing_active.updated_at = timestamp
        candidate.supersedes = existing_active.value
        candidate.created_at = timestamp
        candidate.updated_at = timestamp
        memory.ground_truth_facts.append(candidate)
        changed += 1

    if len(memory.ground_truth_facts) > 50:
        superseded = [f for f in memory.ground_truth_facts if f.status != ACTIVE_STATUS]
        active = [f for f in memory.ground_truth_facts if f.status == ACTIVE_STATUS]
        memory.ground_truth_facts = superseded[-20:] + active[-30:]
    return changed


def apply_ground_truth_updates(
    memory: SessionMemory,
    text: str,
    *,
    source: str = "user",
    now: str | None = None,
) -> int:
    """Extract and merge stable facts from text into a session memory."""
    candidates = extract_ground_truth_facts(text, source=source, now=now)
    return upsert_ground_truth_facts(memory, candidates, now=now)


def resolve_ambiguities_from_ground_truth(
    ambiguities: list[str],
    memory: SessionMemory | None,
    *,
    knowledge_context: str = "",
    user_input: str = "",
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split ambiguity reports into resolved facts, assumptions, and questions.

    Returns ``(memory_resolved, knowledge_resolved, default_assumptions, unresolved)``.
    The matching is intentionally explainable: a resolved item names the ambiguity
    and the fact/default that answered it.
    """
    memory_resolved: list[str] = []
    knowledge_resolved: list[str] = []
    default_assumptions: list[str] = []
    unresolved: list[str] = []
    active_facts = active_ground_truth(memory)

    for ambiguity in ambiguities:
        text = _compact(ambiguity)
        if not text:
            continue
        fact = _best_fact_match(text, active_facts)
        if fact is not None:
            memory_resolved.append(f"{text} -> {ground_truth_to_text(fact)}")
            continue
        if _text_answers_ambiguity(text, user_input):
            default_assumptions.append(f"{text} -> 已由当前请求直接说明")
            continue
        if _text_answers_ambiguity(text, knowledge_context):
            knowledge_resolved.append(f"{text} -> 已由知识库上下文说明")
            continue
        default = _safe_default_for_ambiguity(text)
        if default:
            default_assumptions.append(f"{text} -> {default}")
            continue
        unresolved.append(text)

    return memory_resolved, knowledge_resolved, default_assumptions, unresolved


def prioritize_clarification_questions(questions: list[str]) -> list[str]:
    """Sort unresolved questions by risk and user-impact before applying the cap."""
    return sorted(questions, key=_question_priority, reverse=True)


def _find_active_by_key(facts: list[GroundTruthFact], key: str) -> GroundTruthFact | None:
    for fact in reversed(facts):
        if fact.key == key and fact.status == ACTIVE_STATUS:
            return fact
    return None


def _same_value(left: str, right: str) -> bool:
    return _compact(left).lower() == _compact(right).lower()


def _clean_value(value: str) -> str:
    cleaned = value.strip(" ：:，,。；;")
    correction = _CORRECTION_PREFIX_RE.search(cleaned)
    if correction:
        replacement = correction.group("new") or correction.group("said") or correction.group("future")
        if replacement:
            cleaned = replacement
    return _compact(cleaned)


def _is_stable_value(value: str) -> bool:
    if len(value) < 2 or len(value) > _FACT_LIMIT:
        return False
    ephemeral_markers = ("这次", "本次", "刚才", "现在这个问题", "临时", "一次性")
    return not any(marker in value for marker in ephemeral_markers)


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9_./-]+", lowered))
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    for chunk in zh_chunks:
        words.add(chunk)
        for i in range(max(0, len(chunk) - 1)):
            words.add(chunk[i : i + 2])
    return {w for w in words if w not in _STOPWORDS}


def _best_fact_match(ambiguity: str, facts: list[GroundTruthFact]) -> GroundTruthFact | None:
    if "语言" in ambiguity or "language" in ambiguity.lower():
        for fact in facts:
            if fact.key == "output.language":
                return fact
    if "格式" in ambiguity or "format" in ambiguity.lower():
        for fact in facts:
            if fact.key.startswith("output.format"):
                return fact
    ambiguity_tokens = _tokens(ambiguity)
    best: tuple[float, GroundTruthFact] | None = None
    for fact in facts:
        fact_text = f"{fact.key} {fact.value} {fact.category}"
        fact_tokens = _tokens(fact_text)
        overlap = len(ambiguity_tokens & fact_tokens) / max(1, len(ambiguity_tokens))
        ratio = SequenceMatcher(None, ambiguity.lower(), fact_text.lower()).ratio()
        score = max(overlap, ratio * 0.5)
        if score >= _MATCH_THRESHOLD and (best is None or score > best[0]):
            best = (score, fact)
    return best[1] if best else None


def _text_answers_ambiguity(ambiguity: str, context: str) -> bool:
    if not ambiguity or not context:
        return False
    ambiguity_tokens = _tokens(ambiguity)
    context_tokens = _tokens(context[:_TEXT_LIMIT])
    if not ambiguity_tokens:
        return False
    return len(ambiguity_tokens & context_tokens) / len(ambiguity_tokens) >= 0.5


def _safe_default_for_ambiguity(ambiguity: str) -> str:
    low = ambiguity.lower()
    if any(term in low for term in ("语言", "language")):
        return "未指定语言时沿用用户当前输入语言"
    if any(term in low for term in ("格式", "format", "形式")):
        return "未指定输出格式时默认使用清晰的 Markdown"
    if any(term in low for term in ("详细", "长度", "篇幅", "detail", "length")):
        return "未指定篇幅时默认给出足够完成任务的简洁说明"
    return ""


def _question_priority(question: str) -> int:
    text = question.lower()
    score = 0
    if any(term in text for term in ("删除", "覆盖", "迁移", "安全", "权限", "付费", "不可逆")):
        score += 100
    if any(term in text for term in ("范围", "目标", "对象", "版本", "环境", "路径")):
        score += 40
    if any(term in text for term in ("偏好", "格式", "语言", "风格")):
        score += 20
    return score


__all__ = [
    "ACTIVE_STATUS",
    "SUPERSEDED_STATUS",
    "active_ground_truth",
    "apply_ground_truth_updates",
    "extract_ground_truth_facts",
    "format_ground_truth_for_prompt",
    "ground_truth_to_text",
    "normalize_fact_key",
    "prioritize_clarification_questions",
    "resolve_ambiguities_from_ground_truth",
    "upsert_ground_truth_facts",
]
