"""feishu_doc 聚合工具单测。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


@pytest.mark.asyncio
async def test_feishu_doc_read_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_doc_tools import _feishu_doc
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")

    with (
        patch("miniagent.feishu.docx.client.get_document", return_value={"title": "T", "revision_id": 1}),
        patch("miniagent.feishu.docx.client.get_document_raw_content", return_value="# Hi"),
        patch(
            "miniagent.feishu.docx.blocks.list_document_blocks",
            return_value=([{"block_id": "b1", "block_type": 2}], None, False),
        ),
    ):
        r = await _feishu_doc({"action": "read", "doc_token": "doccnX"}, ToolContext(cwd="/tmp"))
    assert r.success is True
    assert "Hi" in r.content
    assert "block_type_counts" in r.content


@pytest.mark.asyncio
async def test_feishu_doc_unknown_action() -> None:
    from miniagent.tools.feishu_doc_tools import _feishu_doc
    from miniagent.types.tool import ToolContext

    with patch.dict("os.environ", {"FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b"}):
        r = await _feishu_doc({"action": "nope"}, ToolContext(cwd="/tmp"))
    assert r.success is False
    assert "未知 action" in r.content


@pytest.mark.asyncio
async def test_feishu_doc_create_uses_folder_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_doc_tools import _feishu_doc
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.delenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", raising=False)
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "0")
    url = "https://tenant.feishu.cn/drive/folder/fldcnFromShare"

    with patch("miniagent.feishu.docx.client.create_document", return_value=("doc_y", 2)) as mock_create:
        r = await _feishu_doc({"action": "create", "title": "T", "folder_token": url}, ToolContext(cwd="/tmp"))
    assert r.success is True
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["folder_token"] == "fldcnFromShare"
