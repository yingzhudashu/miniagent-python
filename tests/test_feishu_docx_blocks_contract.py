"""Docx block 分页、摘要、清空与批处理的离线契约测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from miniagent.assistant.feishu.docx import blocks
from miniagent.ui.feishu.types import FeishuConfig

CFG = FeishuConfig("app", "secret")


class _Resp:
    def __init__(self, ok=True, data=None, raw=None):
        self._ok = ok
        self.data = data
        self.raw = raw
        self.code = 1
        self.msg = "failed"

    def success(self):
        return self._ok


def test_block_type_summary_chunks_and_paragraphs(monkeypatch) -> None:
    monkeypatch.setattr(blocks, "_TEXT_RUN_MAX", 3)
    assert blocks._block_type_summary(
        [SimpleNamespace(block_type=2), {"block_type": "3"}, {"block_type": "bad"}]
    ) == [2, 3, 0]
    assert blocks._chunk_runs("") == ["\u200b"]
    assert blocks._chunk_runs("abcdefg") == ["abc", "def", "g"]
    paragraphs = blocks._paragraph_blocks_for_text("one\n\nthree")
    assert len(paragraphs) == 3


def test_find_page_block_and_fallback() -> None:
    api = SimpleNamespace()
    client = SimpleNamespace(docx=SimpleNamespace(v1=SimpleNamespace(document_block=api)))
    api.list = lambda _req: _Resp(
        data=SimpleNamespace(
            items=[SimpleNamespace(block_type=1, block_id="page"), SimpleNamespace(block_type=2, block_id="text")]
        )
    )
    assert blocks._find_page_block_id(client, "doc") == "page"
    api.list = lambda _req: _Resp(data=SimpleNamespace(items=[SimpleNamespace(block_type=2, block_id="first")]))
    assert blocks._find_page_block_id(client, "doc") == "first"
    api.list = lambda _req: _Resp(data=SimpleNamespace(items=[SimpleNamespace(block_type=2, block_id=None)]))
    with pytest.raises(RuntimeError, match="empty block_id"):
        blocks._find_page_block_id(client, "doc")
    api.list = lambda _req: _Resp(ok=False)
    with pytest.raises(RuntimeError, match="list document blocks failed"):
        blocks._find_page_block_id(client, "doc")


def test_count_children_paginates_and_detects_stuck_token() -> None:
    responses = [
        _Resp(data=SimpleNamespace(items=[1, 2], has_more=True, page_token="next")),
        _Resp(data=SimpleNamespace(items=[3], has_more=False, page_token=None)),
    ]
    api = SimpleNamespace(get=lambda _req: responses.pop(0))
    client = SimpleNamespace(docx=SimpleNamespace(v1=SimpleNamespace(document_block_children=api)))
    assert blocks._count_children(client, "doc", "page") == 3

    responses = [
        _Resp(data=SimpleNamespace(items=[1], has_more=True, page_token="same")),
        _Resp(data=SimpleNamespace(items=[2], has_more=True, page_token="same")),
    ]
    assert blocks._count_children(client, "doc", "page") == 2
    api.get = lambda _req: _Resp(ok=False)
    with pytest.raises(RuntimeError, match="list block children failed"):
        blocks._count_children(client, "doc", "page")


def test_list_get_and_batch_update(monkeypatch) -> None:
    text_elements = [
        SimpleNamespace(text_run=SimpleNamespace(content="hello")),
        SimpleNamespace(text_run=SimpleNamespace(content=" world")),
    ]
    block = SimpleNamespace(
        block_id="b", block_type=2, parent_id="p", text=SimpleNamespace(elements=text_elements)
    )
    document_api = SimpleNamespace(
        list=lambda _req: _Resp(data=SimpleNamespace(items=[block], page_token="n", has_more=True)),
        get=lambda _req: _Resp(data=SimpleNamespace(block=block)),
        batch_update=lambda _req: _Resp(raw=SimpleNamespace(content=json.dumps({"data": 1}))),
    )
    client = SimpleNamespace(docx=SimpleNamespace(v1=SimpleNamespace(document_block=document_api)))
    monkeypatch.setattr(blocks, "build_client", lambda _cfg: client)
    items, token, more = blocks.list_document_blocks(CFG, "doc", page_token="p", page_size=999)
    assert items[0]["block_id"] == "b" and token == "n" and more
    assert blocks.get_block(CFG, "doc", "b")["text"] == "hello world"
    assert blocks.batch_update_blocks(CFG, "doc", [{"x": 1}]) == {"ok": True, "data": {"data": 1}}
    document_api.get = lambda _req: _Resp(ok=False)
    with pytest.raises(RuntimeError, match="get block failed"):
        blocks.get_block(CFG, "doc", "b")
    document_api.batch_update = lambda _req: _Resp(ok=False)
    with pytest.raises(RuntimeError, match="batch_update failed"):
        blocks.batch_update_blocks(CFG, "doc", [])


def test_clear_document_content_counts_partial_failures(monkeypatch) -> None:
    monkeypatch.setattr(blocks, "build_client", lambda _cfg: object())
    monkeypatch.setattr(blocks, "_find_page_block_id", lambda *_args: "page")
    monkeypatch.setattr(
        blocks,
        "list_document_blocks",
        lambda *_args, **_kwargs: (
            [
                {"block_id": "page", "block_type": 1},
                {"block_id": "", "block_type": 2},
                {"block_id": "skip-page", "block_type": 1},
                {"block_id": "ok", "block_type": 2},
                {"block_id": "bad", "block_type": 2},
            ],
            None,
            False,
        ),
    )

    def delete(_cfg, _doc, block_id):
        if block_id == "bad":
            raise RuntimeError("failed")

    monkeypatch.setattr(blocks, "delete_block", delete)
    assert blocks.clear_document_content_blocks(CFG, "doc") == (1, 1)

