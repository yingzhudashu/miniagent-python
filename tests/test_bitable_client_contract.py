"""Bitable SDK 适配器的成功、分页和错误映射测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.feishu.bitable import client as bitable
from miniagent.feishu.types import FeishuConfig


def _response(data=None, *, success: bool = True):
    return SimpleNamespace(success=lambda: success, data=data, code=1, msg="failure")


def _client() -> MagicMock:
    return MagicMock()


def test_fields_conversion_supports_dict_iterable_and_none() -> None:
    assert bitable._fields_to_dict(None) == {}
    assert bitable._fields_to_dict({"a": 1}) == {"a": 1}
    fields = [SimpleNamespace(field_name="a", value=1), SimpleNamespace(name="b", value=2)]
    assert bitable._fields_to_dict(fields) == {"a": 1, "b": 2}


def test_bitable_read_apis(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(bitable, "build_client", lambda _config: client)
    config = FeishuConfig(app_id="a", app_secret="b")

    client.bitable.v1.app.get.return_value = _response(
        SimpleNamespace(app=SimpleNamespace(app_token="app", name="Demo", url="url"))
    )
    assert bitable.get_app_meta(config, "fallback")["name"] == "Demo"

    client.bitable.v1.app_table.list.return_value = _response(
        SimpleNamespace(
            items=[SimpleNamespace(table_id="t1", name="Table", revision=2)],
            page_token="next",
            has_more=True,
        )
    )
    tables, token, more = bitable.list_tables(config, "app", page_token="p")
    assert tables[0]["table_id"] == "t1" and token == "next" and more

    client.bitable.v1.app_table_field.list.return_value = _response(
        SimpleNamespace(
            items=[SimpleNamespace(field_id="f1", field_name="Name", type=1, is_primary=True)],
            page_token=None,
            has_more=False,
        )
    )
    fields, token, more = bitable.list_fields(config, "app", "t", page_token="p")
    assert fields[0]["is_primary"] and token is None and not more

    record = SimpleNamespace(record_id="r1", fields={"Name": "A"})
    client.bitable.v1.app_table_record.search.return_value = _response(
        SimpleNamespace(items=[record], page_token="next", has_more=True)
    )
    records, token, more = bitable.list_records(
        config,
        "app",
        "t",
        page_token="p",
        page_size=999,
        view_id="v",
        field_names=["Name"],
        filter_expr="CurrentValue.[Name] = \"A\"",
        sort=["Name"],
    )
    assert records == [{"record_id": "r1", "fields": {"Name": "A"}}]
    assert token == "next" and more

    client.bitable.v1.app_table_record.get.return_value = _response(
        SimpleNamespace(record=record)
    )
    assert bitable.get_record(config, "app", "t", "r1")["record_id"] == "r1"


def test_bitable_write_delete_and_attachment_apis(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(bitable, "build_client", lambda _config: client)
    config = FeishuConfig(app_id="a", app_secret="b")
    record = SimpleNamespace(record_id="r1", fields={"Name": "A"})
    client.bitable.v1.app_table_record.create.return_value = _response(
        SimpleNamespace(record=record)
    )
    client.bitable.v1.app_table_record.update.return_value = _response(
        SimpleNamespace(record=record)
    )
    client.bitable.v1.app_table_record.delete.return_value = _response()
    client.bitable.v1.app_table_record.batch_delete.return_value = _response()

    assert bitable.create_record(config, "app", "t", {"Name": "A"})["record_id"] == "r1"
    assert bitable.update_record(config, "app", "t", "r1", {"Name": "B"})["record_id"] == "r1"
    bitable.delete_record(config, "app", "t", "r1")
    assert bitable.delete_records_batch(config, "app", "t", []) == 0
    assert bitable.delete_records_batch(config, "app", "t", [str(i) for i in range(600)]) == 500

    monkeypatch.setattr("miniagent.feishu.docx.media.upload_drive_media", lambda *_a, **_k: "file")
    result = bitable.upload_record_attachment(
        config, "app", "t", "r1", "Attachment", b"data", file_name="a.txt"
    )
    assert result["record_id"] == "r1"


def test_bitable_errors_are_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(bitable, "build_client", lambda _config: client)
    monkeypatch.setattr(bitable, "format_lark_response_error", lambda _response: "code=1")
    client.bitable.v1.app.get.return_value = _response(success=False)
    with pytest.raises(RuntimeError, match="app.get failed"):
        bitable.get_app_meta(FeishuConfig(app_id="a", app_secret="b"), "app")
