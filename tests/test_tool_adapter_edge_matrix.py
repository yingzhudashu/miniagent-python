"""工具适配器的参数校验、错误映射与降级路径。"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from miniagent.tools.schedule_tools import _manage_scheduled_task_handler
from miniagent.types.tool import ToolContext


def _ctx(*, mutable: bool = True, cwd: str = "/tmp") -> ToolContext:
    return ToolContext(cwd=cwd, cli_dispatch_allow_mutations=mutable)


@pytest.mark.asyncio
async def test_schedule_read_validation_and_empty_list(state_dir: str) -> None:
    empty = await _manage_scheduled_task_handler({"action": "list"}, _ctx(mutable=False))
    missing_show = await _manage_scheduled_task_handler({"action": "show"}, _ctx())
    absent_show = await _manage_scheduled_task_handler(
        {"action": "show", "task_id": "absent"}, _ctx()
    )
    missing_remove = await _manage_scheduled_task_handler({"action": "remove"}, _ctx())
    absent_remove = await _manage_scheduled_task_handler(
        {"action": "remove", "task_id": "absent"}, _ctx()
    )

    assert empty.success and "暂无" in empty.content
    assert not missing_show.success and "task_id" in missing_show.content
    assert not absent_show.success and "未找到" in absent_show.content
    assert not missing_remove.success and "task_id" in missing_remove.content
    assert not absent_remove.success and "未找到" in absent_remove.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({}, "缺少 action"),
        ({"action": "set_enabled"}, "task_id"),
        ({"action": "set_enabled", "task_id": "x", "enabled": "yes"}, "布尔值"),
        ({"action": "set_enabled", "task_id": "x", "enabled": True}, "未找到"),
        ({"action": "add_interval"}, "task_id、prompt"),
        (
            {"action": "add_interval", "task_id": "x", "prompt": "p", "interval_seconds": "x"},
            "正整数",
        ),
        (
            {"action": "add_once", "task_id": "x", "prompt": "p"},
            "once_iso",
        ),
        (
            {"action": "add_cron", "task_id": "x", "prompt": "p"},
            "cron_expr",
        ),
        (
            {
                "action": "add_interval",
                "task_id": "x",
                "prompt": "p",
                "interval_seconds": 1,
                "session_mode": "fixed",
            },
            "fixed_session_id",
        ),
        (
            {
                "action": "add_interval",
                "task_id": "x",
                "prompt": "p",
                "interval_seconds": 1,
                "session_mode": "invalid",
            },
            "未知 session_mode",
        ),
        ({"action": "update", "task_id": "x"}, "task_id、prompt"),
        ({"action": "update", "task_id": "x", "prompt": "p"}, "未找到"),
    ],
)
async def test_schedule_parameter_errors(
    state_dir: str, payload: dict[str, object], fragment: str
) -> None:
    result = await _manage_scheduled_task_handler(payload, _ctx())
    assert not result.success
    assert fragment in result.content


@pytest.mark.asyncio
async def test_schedule_once_past_and_update_variants(state_dir: str) -> None:
    past = await _manage_scheduled_task_handler(
        {
            "action": "add_once",
            "task_id": "past",
            "prompt": "p",
            "once_iso": "2000-01-01T00:00:00Z",
            "timezone": "UTC",
        },
        _ctx(),
    )
    assert not past.success and "过去" in past.content

    added = await _manage_scheduled_task_handler(
        {
            "action": "add_interval",
            "task_id": "update-me",
            "prompt": "old",
            "interval_seconds": 60,
        },
        _ctx(),
    )
    assert added.success

    unknown = await _manage_scheduled_task_handler(
        {
            "action": "update",
            "task_id": "update-me",
            "prompt": "new",
            "schedule_kind": "mystery",
        },
        _ctx(),
    )
    missing_cron = await _manage_scheduled_task_handler(
        {
            "action": "update",
            "task_id": "update-me",
            "prompt": "new",
            "schedule_kind": "cron",
        },
        _ctx(),
    )
    enabled = await _manage_scheduled_task_handler(
        {"action": "set_enabled", "task_id": "update-me", "enabled": True}, _ctx()
    )

    assert not unknown.success and "未知 schedule_kind" in unknown.content
    assert not missing_cron.success and "cron_expr" in missing_cron.content
    assert enabled.success


@pytest.mark.asyncio
async def test_bitable_helper_and_dispatch_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("lark_oapi")
    module = importlib.import_module("miniagent.tools.feishu_bitable_tools")

    monkeypatch.setattr(module, "config_from_env", lambda: object())
    monkeypatch.setattr(module, "check_lark_oapi", lambda: None)
    monkeypatch.setattr(module, "extract_bitable_app_token", lambda raw: raw)
    monkeypatch.setattr(module, "extract_table_id", lambda raw, **_kwargs: raw)

    assert module._parse_fields_arg(None) is None
    assert module._parse_fields_arg(1) is None
    assert module._parse_fields_arg(" ") == {}
    assert module._parse_fields_arg('{"a": 1}') == {"a": 1}
    assert module._field_names([1, "a"]) == ["1", "a"]
    assert module._field_names(" a, ,b ") == ["a", "b"]
    assert module._field_names(None) is None

    missing_token = module._feishu_bitable_sync({"action": "get_meta"}, _ctx())
    missing_table = module._feishu_bitable_sync(
        {"action": "list_fields", "app_token": "app"}, _ctx()
    )
    unknown = module._feishu_bitable_sync({"action": "unknown"}, _ctx())
    assert not missing_token.success and "app_token" in missing_token.content
    assert not missing_table.success and "table_id" in missing_table.content
    assert not unknown.success and "未知 action" in unknown.content

    monkeypatch.setattr(module, "list_fields", lambda *_args, **_kwargs: ([{"name": "A"}], "n", True))
    monkeypatch.setattr(module, "list_records", lambda *_args, **_kwargs: ([{"id": "r"}], None, False))
    monkeypatch.setattr(module, "get_record", lambda *_args: {"id": "r"})
    monkeypatch.setattr(module, "update_record", lambda *_args: {"id": "r"})
    monkeypatch.setattr(module, "delete_record", MagicMock())
    monkeypatch.setattr(module, "delete_records_batch", lambda *_args: 2)

    common = {"app_token": "app", "table_id": "table"}
    results = [
        module._feishu_bitable_sync({**common, "action": "list_fields", "page_token": "p"}, _ctx()),
        module._feishu_bitable_sync(
            {
                **common,
                "action": "list_records",
                "page_size": 5,
                "field_names": "A,B",
                "sort": ["A desc"],
            },
            _ctx(),
        ),
        module._feishu_bitable_sync(
            {**common, "action": "get_record", "record_id": "r"}, _ctx()
        ),
        module._feishu_bitable_sync(
            {**common, "action": "update_record", "record_id": "r", "fields": "{}"}, _ctx()
        ),
        module._feishu_bitable_sync(
            {**common, "action": "delete_record", "record_id": "r"}, _ctx()
        ),
        module._feishu_bitable_sync(
            {**common, "action": "delete_record", "record_ids": ["a", "b"]}, _ctx()
        ),
    ]
    assert all(result.success for result in results)

    missing_record = module._feishu_bitable_sync({**common, "action": "get_record"}, _ctx())
    missing_fields = module._feishu_bitable_sync({**common, "action": "create_record"}, _ctx())
    invalid_json = module._feishu_bitable_sync(
        {**common, "action": "create_record", "fields": "{"}, _ctx()
    )
    missing_delete = module._feishu_bitable_sync({**common, "action": "delete_record"}, _ctx())
    missing_upload = module._feishu_bitable_sync({**common, "action": "upload_attachment"}, _ctx())
    assert not missing_record.success and "record_id" in missing_record.content
    assert not missing_fields.success and "fields" in missing_fields.content
    assert not invalid_json.success and "JSON 无效" in invalid_json.content
    assert not missing_delete.success and "record_id" in missing_delete.content
    assert not missing_upload.success and "relative_path" in missing_upload.content

    monkeypatch.setattr(module, "list_fields", MagicMock(side_effect=RuntimeError("sdk down")))
    sdk_error = module._feishu_bitable_sync({**common, "action": "list_fields"}, _ctx())
    assert not sdk_error.success and "sdk down" in sdk_error.content
