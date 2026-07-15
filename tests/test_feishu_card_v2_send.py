"""GFM 表格转为 bullet-point list（poll_server 中的 _normalize_lark_md 路径）。"""

from __future__ import annotations

import pytest

pytest.importorskip("lark_oapi")


def test_normalize_lark_md_wide_table_to_bullet_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有表格（不分宽窄）都应转为 bullet-point list。"""
    from miniagent.assistant.feishu.poll_server import _normalize_lark_md

    # 7 列表格
    md = "| a | b | c | d | e | f | g |\n|---|---|---|---|---|---|---|\n| 1 | 2 | 3 | 4 | 5 | 6 | 7 |"
    result = _normalize_lark_md(md)

    # 不应保留原始 GFM 表格格式
    assert "| a | b |" not in result
    # 应包含 bullet 列表项
    assert "- " in result
    # 不应有代码块包裹
    assert "```" not in result


def test_normalize_lark_md_narrow_table_also_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    """窄表格（列数少）也应转为 bullet list，不再保留原始管道符。"""
    from miniagent.assistant.feishu.poll_server import _normalize_lark_md

    md = "| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |"
    result = _normalize_lark_md(md)

    # 窄表格也应转为 bullet list
    assert "- " in result
    assert "| a | b | c |" not in result  # 不保留原始格式


def test_normalize_lark_md_preserves_other_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.assistant.feishu.poll_server import _normalize_lark_md

    text = "**粗体** 和 `代码` 以及 [链接](https://example.com)"
    result = _normalize_lark_md(text)

    assert "**粗体**" in result
    assert "`代码`" in result
    assert "[链接](https://example.com)" in result


def test_normalize_lark_md_heading_to_bold(monkeypatch: pytest.MonkeyPatch) -> None:
    """ATX 标题转为粗体（lark_md 不支持 ### 语法）。"""
    from miniagent.assistant.feishu.poll_server import _normalize_lark_md

    assert _normalize_lark_md("# 一级标题") == "**一级标题**"
    assert _normalize_lark_md("### 三级标题") == "**三级标题**"
    assert _normalize_lark_md("###### 六级标题") == "**六级标题**"
    # 正文中的 ### 不应被转换
    assert "### text" in _normalize_lark_md("some ### text")


def test_normalize_lark_md_bullet_list_has_key_value_for_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """宽表格使用 key-value 格式的 bullet list。"""
    from miniagent.assistant.feishu.poll_server import _normalize_lark_md

    md = "| 姓名 | 年龄 | 城市 | 职业 | 部门 | 级别 | 评分 |\n|---|---|---|---|---|---|---|\n| 张三 | 28 | 北京 | 工程师 | 技术部 | P6 | 95 |"
    result = _normalize_lark_md(md)

    assert "- **" in result  # key-value 格式以 ** 开头
    assert "姓名" in result  # 包含表头信息


def test_gfm_table_block_to_bullet_list_key_value_format() -> None:
    """直接测试 gfm_table_block_to_bullet_list 的 key-value 格式。"""
    from miniagent.assistant.feishu.cards.gfm_table import gfm_table_block_to_bullet_list

    lines = [
        "| A | B | C | D | E | F | G |",
        "|---|---|---|---|---|---|---|",
        "| 1 | 2 | 3 | 4 | 5 | 6 | 7 |",
        "| 8 | 9 | 10 | 11 | 12 | 13 | 14 |",
    ]
    result = gfm_table_block_to_bullet_list(lines)
    assert result.startswith("- **")
    assert "A" in result
    assert "→" in result


def test_gfm_table_block_to_bullet_list_pipe_format() -> None:
    """直接测试 gfm_table_block_to_bullet_list 的简洁管道符格式（<= 6 列）。"""
    from miniagent.assistant.feishu.cards.gfm_table import gfm_table_block_to_bullet_list

    lines = [
        "| a | b | c |",
        "|---|---|---|",
        "| 1 | 2 | 3 |",
        "| 4 | 5 | 6 |",
    ]
    result = gfm_table_block_to_bullet_list(lines)
    assert result.startswith("- ")
    assert " | " in result  # 简洁格式用 | 分隔
