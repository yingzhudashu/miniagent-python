"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from miniagent.assistant.feishu.feishu_dedup import FeishuDeduplicator
from miniagent.ui.feishu.types import FeishuConfig


def test_deduplicator_evicts_oldest_completed_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.assistant.feishu.feishu_dedup as dedup_module

    monkeypatch.setattr(dedup_module, "DEDUP_MAX_SIZE", 2)
    dedup = FeishuDeduplicator(str(tmp_path))
    monkeypatch.setattr(dedup, "_maybe_schedule_flush", lambda: None)
    for message_id in ("one", "two", "three"):
        assert dedup.try_begin_processing(message_id)
        dedup.release_processing(message_id)
    assert len(dedup._processed) <= 2
    assert "mini-agent:one" not in dedup._processed

@pytest.mark.asyncio
async def test_feishu_im_handlers_cover_config_and_filter_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.assistant.feishu import upload_io

    im_tools = importlib.import_module("miniagent.assistant.tools.feishu_im_tools")
    cfg = FeishuConfig("id", "secret")
    monkeypatch.setattr(im_tools, "check_feishu_config_and_lark_oapi", lambda: (cfg, None))
    monkeypatch.setattr(im_tools, "default_receive_id_for_send", lambda *_args: ("chat", None))
    monkeypatch.setattr(im_tools, "effective_receive_id_type", lambda *_args: "chat_id")
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(upload_io, "upload_im_image", lambda *_args: "img-key")
    monkeypatch.setattr(upload_io, "send_im_image_message", lambda *_args, **_kwargs: (True, None))
    ctx = SimpleNamespace(cwd=str(tmp_path))
    sent = await im_tools._feishu_send_workspace_file(
        {"relative_path": "image.png", "as_image": True}, ctx
    )
    assert sent.success

    monkeypatch.setattr(upload_io, "delete_im_message", lambda *_args: (True, None))
    recalled = await im_tools._feishu_recall_message({"message_id": "m1"}, ctx)
    assert recalled.success

    async def resolved(*_args, **_kwargs):
        return "folder", None

    monkeypatch.setattr(im_tools, "resolve_parent_folder_token_async", resolved)
    import miniagent.assistant.feishu.drive_client as drive_client

    monkeypatch.setattr(
        drive_client,
        "list_folder_files_page",
        lambda *_args, **_kwargs: (
            [
                {"name": "skip", "token": "f1", "type": "file"},
                {"name": "Keep|Folder", "token": "t|2", "type": "folder"},
            ],
            None,
            False,
        ),
    )
    listed = await im_tools._feishu_list_drive_files(
        {"folders_only": True, "name_contains": "keep"}, ctx
    )
    assert listed.success
    assert "Keep\\|Folder" in listed.content

    monkeypatch.setattr(
        drive_client,
        "list_folder_files_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("API down")),
    )
    failed = await im_tools._feishu_list_drive_files({}, ctx)
    assert not failed.success
    assert "API down" in failed.content

def test_feishu_doc_table_values_are_validated_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.feishu.docx.tables as tables
    from miniagent.assistant.tools.feishu_doc_tools import _action_write_table_cells

    written: list[list[list[str]]] = []
    monkeypatch.setattr(
        tables,
        "write_table_cells",
        lambda _cfg, _doc, _table, values: written.append(values),
    )
    cfg = FeishuConfig("id", "secret")
    invalid = _action_write_table_cells(
        {"doc_token": "doc", "table_block_id": "table", "values": "{}"}, cfg
    )
    assert not invalid.success
    valid = _action_write_table_cells(
        {"doc_token": "doc", "table_block_id": "table", "values": [[1, "x"]]}, cfg
    )
    assert valid.success
    assert written == [[['1', 'x']]]

def test_feishu_ws_client_initializes_owned_task_state() -> None:
    from miniagent.assistant.feishu.ws_client import FeishuWsClient

    client = FeishuWsClient(
        app_id="id", app_secret="secret", event_handler=lambda _event: None
    )
    assert client.receive_task is None
