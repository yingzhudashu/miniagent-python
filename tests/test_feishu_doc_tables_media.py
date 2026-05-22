"""feishu_doc 表格/媒体 action 路由（mock）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


@pytest.mark.asyncio
async def test_feishu_doc_create_table(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_doc_tools import _feishu_doc
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    with patch("miniagent.feishu.docx.tables.create_table_block", return_value="tbl_1"):
        r = await _feishu_doc(
            {"action": "create_table", "doc_token": "d1", "row_size": 2, "column_size": 3},
            ToolContext(cwd="/tmp"),
        )
    assert r.success is True
    assert "tbl_1" in r.content


@pytest.mark.asyncio
async def test_feishu_doc_download_media(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_doc_tools import _feishu_doc
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    ws = str(tmp_path)
    with patch("miniagent.feishu.docx.media.download_media_bytes", return_value=b"png"):
        r = await _feishu_doc(
            {"action": "download_media", "file_token": "ftok", "relative_path": "out.bin"},
            ToolContext(cwd=ws),
        )
    assert r.success is True
    assert (tmp_path / "out.bin").read_bytes() == b"png"


@pytest.mark.asyncio
async def test_feishu_doc_write_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_doc_tools import _feishu_doc
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    with (
        patch("miniagent.feishu.docx.blocks.clear_document_content_blocks", return_value=(3, 1)),
        patch("miniagent.feishu.docx.blocks.append_plain_text_to_document", return_value=2),
    ):
        r = await _feishu_doc(
            {"action": "write", "doc_token": "d1", "content": "# New", "mode": "replace"},
            ToolContext(cwd="/tmp"),
        )
    assert r.success is True
    assert "replace" in r.content
    assert "删除失败" in r.content or "1 个块删除失败" in r.content


def test_remove_permission_mock() -> None:
    from unittest.mock import MagicMock, patch

    from miniagent.feishu.drive_extra import remove_permission
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    with patch("miniagent.feishu.drive_extra.build_client") as bc:
        bc.return_value.drive.v1.permission_member.delete.return_value = mock_resp
        remove_permission(cfg, "doc_tok", member_type="email", member_id="u@x.com")
