"""Tests for solid ground truth memory helpers."""

from __future__ import annotations

from miniagent.memory.ground_truth import (
    active_ground_truth,
    apply_ground_truth_updates,
    extract_ground_truth_facts,
    format_ground_truth_for_prompt,
    resolve_ambiguities_from_ground_truth,
)
from miniagent.types.memory import GroundTruthFact, SessionMemory


def test_extract_stable_output_format_fact() -> None:
    facts = extract_ground_truth_facts("记住以后回复都用中文 Markdown，尽量详细")

    assert facts
    assert any(f.key.startswith("output.") for f in facts)
    assert any("中文" in f.value or "Markdown" in f.value for f in facts)


def test_ephemeral_text_is_not_promoted() -> None:
    facts = extract_ground_truth_facts("这次临时用英文回答")

    assert facts == []


def test_upsert_supersedes_previous_value() -> None:
    memory = SessionMemory(session_id="s")

    apply_ground_truth_updates(memory, "以后回复都用中文", now="2026-01-01T00:00:00+00:00")
    apply_ground_truth_updates(memory, "纠正一下，以后回复都用英文", now="2026-01-02T00:00:00+00:00")

    active = active_ground_truth(memory)
    assert len(active) == 1
    assert "英文" in active[0].value
    assert active[0].supersedes is not None
    assert any(f.status == "superseded" for f in memory.ground_truth_facts)


def test_resolve_ambiguity_from_ground_truth() -> None:
    memory = SessionMemory(
        session_id="s",
        ground_truth_facts=[
            GroundTruthFact(
                key="output.language",
                value="默认用中文回答",
                category="output_format",
                confidence=0.95,
            )
        ],
    )

    memory_resolved, knowledge_resolved, defaults, unresolved = resolve_ambiguities_from_ground_truth(
        ["输出语言是什么", "输出格式是什么", "目标目录是哪一个"],
        memory,
    )

    assert any("output.language" in item for item in memory_resolved)
    assert any("Markdown" in item for item in defaults)
    assert "目标目录是哪一个" in unresolved
    assert knowledge_resolved == []


def test_format_ground_truth_for_prompt_only_active_high_confidence() -> None:
    memory = SessionMemory(
        session_id="s",
        ground_truth_facts=[
            GroundTruthFact(key="a", value="active", confidence=0.9),
            GroundTruthFact(key="b", value="low", confidence=0.5),
            GroundTruthFact(key="c", value="old", status="superseded", confidence=0.95),
        ],
    )

    assert format_ground_truth_for_prompt(memory) == ["a: active"]
