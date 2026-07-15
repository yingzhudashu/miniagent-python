"""Regression tests for Feishu Docx rich block validation fallback."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


class _Resp:
    def __init__(self, ok: bool, *, code: int | None = None, msg: str = "", log_id: str = ""):
        self._ok = ok
        self.code = code
        self.msg = msg
        self.log_id = log_id

    def success(self) -> bool:
        return self._ok


class _CreateApi:
    def __init__(self, responses: list[_Resp]):
        self._responses = responses
        self.calls = 0

    def create(self, _req):
        self.calls += 1
        return self._responses.pop(0)


class _Client:
    def __init__(self, responses: list[_Resp]):
        self.docx = SimpleNamespace(
            v1=SimpleNamespace(document_block_children=_CreateApi(responses))
        )


def test_batch_create_blocks_raises_on_first_invalid_param(monkeypatch: pytest.MonkeyPatch):
    from miniagent.assistant.feishu.docx import blocks
    from miniagent.assistant.feishu.types import FeishuConfig

    client = _Client([_Resp(False, code=1770001, msg="invalid param", log_id="log_x")])
    monkeypatch.setattr(blocks, "build_client", lambda _cfg: client)
    monkeypatch.setattr(blocks, "_find_page_block_id", lambda *_args: "page_1")
    monkeypatch.setattr(blocks, "_count_children", lambda *_args: 0)

    with pytest.raises(blocks.DocxBlockCreateError) as exc:
        blocks._batch_create_blocks(FeishuConfig("a", "b"), "doc_1", [object()])

    message = str(exc.value)
    assert "1770001" in message
    assert "invalid param" in message
    assert "block_types" in message
    assert exc.value.created_count == 0


def test_batch_create_blocks_returns_partial_success_warning(monkeypatch: pytest.MonkeyPatch):
    from miniagent.assistant.feishu.docx import blocks
    from miniagent.assistant.feishu.types import FeishuConfig

    client = _Client(
        [_Resp(True), _Resp(False, code=99992402, msg="field validation failed")]
    )
    monkeypatch.setattr(blocks, "DOCX_APPEND_MAX_BLOCKS", 1)
    monkeypatch.setattr(blocks, "build_client", lambda _cfg: client)
    monkeypatch.setattr(blocks, "_find_page_block_id", lambda *_args: "page_1")
    monkeypatch.setattr(blocks, "_count_children", lambda *_args: 0)

    created, warnings = blocks._batch_create_blocks(
        FeishuConfig("a", "b"), "doc_1", [object(), object()]
    )

    assert created == 1
    assert any("99992402" in warning for warning in warnings)
    assert any("partially failed" in warning for warning in warnings)


def test_append_markdown_falls_back_to_plain_text_on_invalid_param(
    monkeypatch: pytest.MonkeyPatch,
):
    from miniagent.assistant.feishu.docx import blocks
    from miniagent.assistant.feishu.types import FeishuConfig

    monkeypatch.setattr(
        blocks,
        "_batch_create_blocks",
        lambda *_args: (_ for _ in ()).throw(
            blocks.DocxBlockCreateError(
                "Feishu create block children failed: code=1770001 msg=invalid param"
            )
        ),
    )

    with patch.object(blocks, "append_plain_text_to_document", return_value=2) as plain:
        written, warnings = blocks.append_markdown_to_document(
            FeishuConfig("a", "b"), "doc_1", "# Title\n\nbody", use_renderer=True
        )

    assert written == 2
    plain.assert_called_once()
    joined = "\n".join(warnings)
    assert "1770001" in joined
    assert "fallback" in joined


def test_append_markdown_reports_plain_fallback_failure(monkeypatch: pytest.MonkeyPatch):
    from miniagent.assistant.feishu.docx import blocks
    from miniagent.assistant.feishu.types import FeishuConfig

    monkeypatch.setattr(
        blocks,
        "_batch_create_blocks",
        lambda *_args: (_ for _ in ()).throw(
            blocks.DocxBlockCreateError(
                "Feishu create block children failed: code=1770001 msg=invalid param"
            )
        ),
    )

    with patch.object(blocks, "append_plain_text_to_document", side_effect=RuntimeError("plain failed")):
        written, warnings = blocks.append_markdown_to_document(
            FeishuConfig("a", "b"), "doc_1", "# Title", use_renderer=True
        )

    assert written == 0
    joined = "\n".join(warnings)
    assert "plain-text fallback also failed" in joined
    assert "plain failed" in joined


def test_docx_render_trace_is_metrics_only():
    from miniagent.agent.observability import clear_trace_hooks, register_trace_hook
    from miniagent.assistant.tools.feishu_doc_tools import _trace_docx_render

    events: list[dict] = []
    clear_trace_hooks()
    register_trace_hook(events.append)
    try:
        _trace_docx_render(
            "append",
            "rich",
            {"written_blocks": 2, "fallback_count": 1},
            ["rich block creation failed: code=1770001 msg=invalid param"],
        )
    finally:
        clear_trace_hooks()

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "feishu.docx_render"
    assert event["written_blocks"] == 2
    assert event["fallback_count"] == 1
    assert event["validation_error"] is True
    assert "doc_" not in repr(event)
    assert "content" not in event
    assert "markdown" not in event


def test_append_markdown_with_stats_parses_once(monkeypatch: pytest.MonkeyPatch):
    from miniagent.assistant.feishu.docx import blocks, markdown_renderer
    from miniagent.assistant.feishu.types import FeishuConfig

    monkeypatch.setattr(
        blocks,
        "_batch_create_blocks",
        lambda _config, _document_id, lark_blocks: (len(lark_blocks), []),
    )
    with patch.object(
        markdown_renderer,
        "markdown_to_feishu_blocks",
        wraps=markdown_renderer.markdown_to_feishu_blocks,
    ) as render:
        written, warnings, stats = blocks.append_markdown_to_document_with_stats(
            FeishuConfig("a", "b"),
            "doc_1",
            "# Title\n\nbody",
    )

    assert written == 2
    assert len(warnings) == 1
    assert stats["total_blocks"] == 2
    assert stats["warnings"] == 1
    assert render.call_count == 1
