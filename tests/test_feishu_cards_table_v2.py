"""CARD_V2 宽表抽取与构建。"""

from __future__ import annotations

from miniagent.assistant.feishu.cards.table_v2 import build_v2_table_card, extract_wide_gfm_table


def test_extract_wide_gfm_table() -> None:
    md = "| a | b | c | d | e | f | g | h |\n|---|---|---|---|---|---|---|---|\n| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |"
    rows = extract_wide_gfm_table(md, max_pipes=4)
    assert rows is not None
    assert len(rows) >= 2


def test_build_v2_table_card_schema() -> None:
    rows = [["H1", "H2"], ["v1", "v2"]]
    card = build_v2_table_card(rows, max_rows=10, max_cols=4)
    assert card.get("schema") == "2.0"
    body = card.get("body") or {}
    els = body.get("elements") or []
    assert els and els[0].get("tag") == "table"
