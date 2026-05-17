"""feishu_im_tools 处理器（非 create_document）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


@pytest.mark.asyncio
async def test_feishu_get_document_markdown_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.tools.feishu_im_tools import _feishu_get_document_markdown
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")

    with patch(
        "miniagent.feishu.docx_client.get_document_raw_content",
        return_value="# Title\n\nbody",
    ):
        r = await _feishu_get_document_markdown(
            {"document_id": "doc_xyz"},
            ToolContext(cwd="/tmp"),
        )
    assert r.success is True
    assert "Title" in r.content
