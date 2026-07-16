"""飞书回复按卡片正文上限分片。"""

from __future__ import annotations


def test_chunk_concat_roundtrip() -> None:
    from miniagent.assistant.feishu.card_rendering import chunk_card_markdown

    s = "a" * 35
    parts = chunk_card_markdown(s, max_len=12)
    assert "".join(parts) == s
    assert all(len(p) <= 12 for p in parts)


def test_chunk_multiline_produces_multiple_segments() -> None:
    from miniagent.assistant.feishu.card_rendering import chunk_card_markdown

    s = "para1\n\npara2\n\npara3\nextra-long-tail-xxxxx"
    parts = chunk_card_markdown(s, max_len=18)
    assert len(parts) >= 2
    assert all(len(p) <= 18 for p in parts)


def test_single_chunk_when_under_cap() -> None:
    from miniagent.assistant.feishu.card_rendering import chunk_card_markdown

    assert chunk_card_markdown("hello", max_len=1000) == ["hello"]
