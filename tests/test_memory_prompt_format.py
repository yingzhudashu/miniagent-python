"""结构化记忆到提示词的格式化、截断和事实提取测试。"""

from __future__ import annotations

from miniagent.agent.types.memory import FileMetadata, GroundTruthFact, MemoryEntry, SessionMemory
from miniagent.assistant.memory.store import (
    extract_facts,
    format_memory_for_prompt,
    generate_turn_summary,
)


def test_format_memory_includes_all_sections_and_deduplicates_ground_truth() -> None:
    memory = SessionMemory(
        session_id="s",
        cumulative_summary="此前讨论了架构",
        key_facts=["使用中文", "偏好 Markdown"],
        ground_truth_facts=[GroundTruthFact(key="output.language", value="使用中文")],
        uploaded_files=[
            FileMetadata(
                name="large.png",
                path="large.png",
                size=2048,
                mime_type="image/png",
                type="image",
                description="描述" * 120,
            ),
            FileMetadata(
                name="small.txt",
                path="small.txt",
                size=12,
                mime_type="text/plain",
                type="text",
            ),
        ],
        entries=[MemoryEntry("2026-07-13T01:02:03", "问题", "回答")],
    )
    text = format_memory_for_prompt(memory)
    assert "确定事实" in text and "上传的文件" in text and "之前的对话摘要" in text
    assert "2KB" in text and "12B" in text and "最近的对话" in text
    assert text.count("使用中文") == 1
    assert "…" in text


def test_format_empty_memory_and_fact_extraction() -> None:
    assert format_memory_for_prompt(None) == ""
    assert format_memory_for_prompt(SessionMemory(session_id="s")) == ""
    facts = extract_facts("记住：使用中文。以后：默认 Markdown。偏好：简洁。喜欢咖啡。不喜欢噪音。")
    assert any("中文" in fact for fact in facts)
    assert any("Markdown" in fact for fact in facts)
    assert extract_facts("普通陈述") == []


def test_generate_turn_summary_handles_optional_parts_and_truncates() -> None:
    summary = generate_turn_summary(
        "创建文件" * 20,
        [{"name": "write_file"}, {"name": "read_file"}],
        "完成" * 100,
    )
    assert "write_file, read_file" in summary and "回复:" in summary
    assert len(summary) < 220
    assert generate_turn_summary("", [], "") == ""

