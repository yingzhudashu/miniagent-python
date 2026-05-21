"""feishu_bitable 聚合工具单测。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("lark_oapi")


@pytest.mark.asyncio
async def test_feishu_bitable_get_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_bitable_tools import _feishu_bitable
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")

    with (
        patch(
            "miniagent.tools.feishu_bitable_tools.get_app_meta",
            return_value={"app_token": "appX", "name": "Demo"},
        ),
        patch(
            "miniagent.tools.feishu_bitable_tools.list_tables",
            return_value=([{"table_id": "tbl1", "name": "Table1"}], None, False),
        ),
    ):
        r = await _feishu_bitable({"action": "get_meta", "app_token": "appX"}, ToolContext(cwd="/tmp"))
    assert r.success is True
    assert "Demo" in r.content
    assert "tbl1" in r.content


@pytest.mark.asyncio
async def test_feishu_bitable_create_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_bitable_tools import _feishu_bitable
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")

    with patch(
        "miniagent.tools.feishu_bitable_tools.create_record",
        return_value={"record_id": "rec1", "fields": {"名称": "a"}},
    ):
        r = await _feishu_bitable(
            {
                "action": "create_record",
                "app_token": "appX",
                "table_id": "tbl1",
                "fields": {"名称": "a"},
            },
            ToolContext(cwd="/tmp"),
        )
    assert r.success is True
    assert "rec1" in r.content


@pytest.mark.asyncio
async def test_feishu_bitable_upload_attachment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.tools.feishu_bitable_tools import _feishu_bitable
    from miniagent.types.tool import ToolContext

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    with patch(
        "miniagent.tools.feishu_bitable_tools.upload_record_attachment",
        return_value={"record_id": "rec1", "fields": {"附件": []}},
    ) as mock_up:
        r = await _feishu_bitable(
            {
                "action": "upload_attachment",
                "app_token": "appX",
                "table_id": "tbl1",
                "record_id": "rec1",
                "field_name": "附件",
                "relative_path": "a.txt",
            },
            ToolContext(cwd=str(tmp_path)),
        )
    assert r.success is True
    mock_up.assert_called_once()
