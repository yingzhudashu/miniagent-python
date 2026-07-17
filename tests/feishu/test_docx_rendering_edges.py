"""Focused regressions migrated from test_diff_gate_new_modules.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_docx_rendered_table_and_block_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.feishu.docx.markdown_renderer as renderer
    import miniagent.assistant.feishu.docx.tables as tables
    from miniagent.assistant.feishu.docx import blocks
    from miniagent.ui.feishu.types import FeishuConfig

    warnings: list[str] = []
    empty = SimpleNamespace(table_data=[])
    zero_columns = SimpleNamespace(table_data=[[]])
    valid = SimpleNamespace(
        table_data=[
            [SimpleNamespace(content="a"), SimpleNamespace(content="b")],
            [SimpleNamespace(content="c")],
        ]
    )
    failed = SimpleNamespace(table_data=[[SimpleNamespace(content="x")]])
    create = MagicMock(side_effect=[None, RuntimeError("table failed")])
    monkeypatch.setattr(tables, "create_table_with_values", create)
    success, failure = blocks._append_rendered_tables(
        FeishuConfig("a", "b"), "doc", [empty, zero_columns, valid, failed], warnings
    )
    assert (success, failure) == (1, 1)
    assert create.call_args_list[0].kwargs["values"] == [["a", "b"], ["c", ""]]
    assert "table failed" in warnings[0]

    assert blocks._append_rendered_blocks(FeishuConfig("a", "b"), "doc", [], warnings) == 0
    monkeypatch.setattr(renderer, "build_lark_blocks_from_intermediate", lambda _items: [])
    assert blocks._append_rendered_blocks(
        FeishuConfig("a", "b"), "doc", [object()], warnings
    ) == 0
    monkeypatch.setattr(renderer, "build_lark_blocks_from_intermediate", lambda _items: ["block"])
    monkeypatch.setattr(blocks, "_batch_create_blocks", lambda *_args: (1, ["warning"]))
    assert blocks._append_rendered_blocks(
        FeishuConfig("a", "b"), "doc", [object()], warnings
    ) == 1
    assert warnings[-1] == "warning"
