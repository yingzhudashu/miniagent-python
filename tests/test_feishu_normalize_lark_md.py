"""飞书 lark_md 正文规范化。"""

from __future__ import annotations

import pytest


def testprepare_card_markdown_collapses_long_fence() -> None:
    from miniagent.assistant.feishu.card_rendering import prepare_card_markdown

    raw = "````python\nx = 1\n````\n"
    out = prepare_card_markdown(raw, max_len=10_000)
    assert "````" not in out
    assert "```python" in out


def testprepare_card_markdown_wide_table_to_bullet_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.assistant.feishu.card_rendering import prepare_card_markdown

    header = "|a|b|c|d|e|"
    sep = "|---|---|---|---|---|"
    row = "|1|2|3|4|5|"
    raw = f"intro\n\n{header}\n{sep}\n{row}\n"
    out = prepare_card_markdown(raw, max_len=10_000)
    # 表格应转为 bullet list，不再用代码块或警告提示
    assert "- " in out
    assert "```" not in out
    assert "列数较多" not in out
    assert header not in out


def testprepare_card_markdown_narrow_table_also_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """窄表格也应转为 bullet list（lark_md 不支持任何管道符表格）。"""
    from miniagent.assistant.feishu.card_rendering import prepare_card_markdown

    raw = "|a|b|c|\n|---|---|---|\n|1|2|3|\n"
    out = prepare_card_markdown(raw, max_len=10_000)
    # 窄表格也应转为 bullet list
    assert "- " in out
    assert "|a|b|c|" not in out


def testprepare_card_markdown_heading_to_bold() -> None:
    """ATX 标题转为粗体。"""
    from miniagent.assistant.feishu.card_rendering import prepare_card_markdown

    out = prepare_card_markdown("### 三级标题", max_len=10_000)
    assert "**三级标题**" in out
    assert "###" not in out


def testprepare_card_markdown_preserves_pipe_prose_without_table_row() -> None:
    """含 | 的正文但下一行不是表格分隔行时不应整块替换为「列数较多」提示。"""
    from miniagent.assistant.feishu.card_rendering import prepare_card_markdown

    raw = (
        "|many|pipes|in|one|line|without|being|a|full|markdown|table|row|\n"
        "This line is clearly not a GFM separator.\n"
    )
    out = prepare_card_markdown(raw, max_len=10_000)
    assert "列数较多" not in out
    assert "This line is clearly" in out


def test_chunk_feishu_normalizes_before_split(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.assistant.feishu import card_rendering as ps

    monkeypatch.setattr(ps, "feishu_card_body_max", lambda: 200)
    raw = "\u200b# Title\n\n" + ("paragraph\n" * 80)
    chunks = ps.chunk_card_markdown(raw)
    assert len(chunks) >= 2
    assert "\u200b" not in "".join(chunks)


def testprepare_thinking_markdown_collapses_blank_lines() -> None:
    from miniagent.assistant.feishu.card_rendering import prepare_thinking_markdown

    raw = "第一段\n\n\n\n第二段"
    out = prepare_thinking_markdown(raw)
    assert "\n\n\n" not in out
    assert "第一段" in out and "第二段" in out
    assert "    第一段" not in out


def testprepare_thinking_markdown_list_lines_not_padded() -> None:
    from miniagent.assistant.feishu.card_rendering import prepare_thinking_markdown

    raw = "**工具**\n\n- first intent\n- second intent"
    out = prepare_thinking_markdown(raw)
    assert "- first intent" in out and "- second intent" in out
    assert "    - first intent" not in out


def testprepare_thinking_markdown_ordered_list_not_padded() -> None:
    from miniagent.assistant.feishu.card_rendering import prepare_thinking_markdown

    raw = "步骤\n\n1. 先做 A\n2. 再做 B"
    out = prepare_thinking_markdown(raw)
    assert "1. 先做 A" in out and "2. 再做 B" in out
    assert "    1. 先做 A" not in out


def testprepare_card_markdown_skips_normalize_when_requested() -> None:
    from miniagent.assistant.feishu.card_rendering import prepare_card_markdown

    raw = "a * b"
    with_norm = prepare_card_markdown(raw, max_len=10_000)
    no_norm = prepare_card_markdown(raw, max_len=10_000, normalize=False)
    assert "\uff0a" in with_norm
    assert "\uff0a" not in no_norm


def test_finalize_pipeline_matches_streaming_thinking_prep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收尾应对累积正文走与 PATCH 相同的 thinking body 管线（折叠空行与 normalize，无额外缩进）。"""
    from miniagent.assistant.feishu import card_rendering as ps

    monkeypatch.setattr(ps, "feishu_card_body_max", lambda: 10_000)
    acc = "段落一\n\n段落二\n\n**工具**\n\n- a\n- b"
    prep = ps.prepare_thinking_body_for_card(acc, apply_cap=False)
    chunks = ps.chunk_card_markdown(prep, already_normalized=True)
    assert len(chunks) == 1
    assert "段落一" in chunks[0] and "段落二" in chunks[0]
    assert "- a" in chunks[0] and "- b" in chunks[0]
    assert "    段落一" not in chunks[0]


def testnormalize_lark_md_horizontal_rule_to_plain_line() -> None:
    from miniagent.assistant.feishu.card_rendering import normalize_lark_md

    assert "---" not in normalize_lark_md("a\n\n---\n\nb")
    assert "\u2500\u2500\u2500" in normalize_lark_md("a\n\n---\n\nb")


def teststrip_light_markdown_for_plain() -> None:
    from miniagent.assistant.feishu.card_rendering import strip_light_markdown_for_plain

    s = strip_light_markdown_for_plain("**bold** and `x`")
    assert "**" not in s
    assert "bold" in s
    assert "`" not in s


def testnormalize_lark_md_lone_asterisk_to_fullwidth() -> None:
    from miniagent.assistant.feishu.card_rendering import normalize_lark_md

    assert "\uff0a" in normalize_lark_md("a * b")
    assert "**bold**" in normalize_lark_md("**bold**")


def testnormalize_lark_md_strips_replacement_char() -> None:
    from miniagent.assistant.feishu.card_rendering import normalize_lark_md

    assert "\ufffd" not in normalize_lark_md("x\ufffdy")


def test_chunk_feishu_extends_past_cap_to_close_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.assistant.feishu import card_rendering as ps

    monkeypatch.setattr(ps, "feishu_card_body_max", lambda: 120)
    inner = "\n".join([f"line {i}" for i in range(40)])
    raw = f"intro\n\n```\n{inner}\n```\n\ntrailer\n"
    chunks = ps.chunk_card_markdown(raw)
    assert len(chunks) >= 1
    first = chunks[0]
    assert first.count("```") >= 2
    assert first.rstrip().endswith("```")
