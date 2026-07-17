"""飞书 Docx 聚合工具各 action 的离线适配矩阵测试。"""

from __future__ import annotations

import importlib
import json

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.ui.feishu.types import FeishuConfig

CFG = FeishuConfig("app", "secret")
tools = importlib.import_module("miniagent.assistant.tools.feishu_doc_tools")


@pytest.mark.asyncio
async def test_doc_top_level_config_and_exception_mapping(monkeypatch) -> None:
    monkeypatch.setattr(tools, "config_from_env", lambda: None)
    missing = await tools._feishu_doc({"action": "get"}, ToolContext(cwd=""))
    assert not missing.success and "未配置" in missing.content

    monkeypatch.setattr(tools, "config_from_env", lambda: CFG)
    monkeypatch.setattr(tools, "check_lark_oapi", lambda: None)
    monkeypatch.setattr(
        tools, "_dispatch_sync_doc_action", lambda *_args: (_ for _ in ()).throw(RuntimeError("sdk"))
    )
    failed = await tools._feishu_doc({"action": "get"}, ToolContext(cwd=""))
    assert not failed.success and "sdk" in failed.content


def test_doc_export_and_download_require_workspace_paths() -> None:
    export_missing_doc = tools._action_export_raw({}, ToolContext(cwd=""), CFG)
    export_missing_path = tools._action_export_raw(
        {"doc_token": "doc"}, ToolContext(cwd=""), CFG
    )
    download_missing_path = tools._action_download_media(
        {"file_token": "file"}, ToolContext(cwd=""), CFG
    )
    assert not export_missing_doc.success and "doc_token" in export_missing_doc.content
    assert not export_missing_path.success and "relative_path" in export_missing_path.content
    assert not download_missing_path.success and "relative_path" in download_missing_path.content


def test_doc_url_and_trace_validation(monkeypatch) -> None:
    emitted = []
    import miniagent.agent.observability as tracing

    monkeypatch.setattr(tools, "get_config", lambda key, default=None: "https://tenant/docx")
    monkeypatch.setattr(tracing, "emit_trace", emitted.append)
    assert tools._docx_open_url("doc1") == "https://tenant/docx/doc1"
    assert tools._docx_open_url("") is None
    tools._trace_docx_render("write", "rich", {"written_blocks": 2}, ["1770001"])
    assert emitted[0]["validation_error"] is True


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("get", {}),
        ("read", {}),
        ("delete", {}),
        ("list_blocks", {}),
        ("get_block", {"doc_token": "doc"}),
        ("update_block", {"doc_token": "doc"}),
        ("delete_block", {"doc_token": "doc"}),
        ("batch_update", {}),
        ("create_table", {}),
        ("write_table_cells", {"doc_token": "doc"}),
        ("create_table_with_values", {}),
        ("upload_image", {}),
        ("upload_file", {}),
        ("download_media", {}),
        ("list_permissions", {}),
        ("add_permission", {"doc_token": "doc"}),
        ("remove_permission", {"doc_token": "doc"}),
        ("search", {}),
    ],
)
def test_doc_actions_validate_required_arguments(action, args) -> None:
    result = tools._dispatch_sync_doc_action(action, args, ToolContext(cwd=""), CFG)
    assert result.success is False


def test_metadata_blocks_and_batch_actions(monkeypatch) -> None:
    import miniagent.assistant.feishu.docx.blocks as blocks
    import miniagent.assistant.feishu.docx.client as client

    calls = []
    monkeypatch.setattr(client, "get_document", lambda *_args: {"title": "T"})
    monkeypatch.setattr(client, "delete_document", lambda *_args: calls.append("delete"))
    monkeypatch.setattr(blocks, "list_document_blocks", lambda *_args, **_kw: ([{"id": 1}], "n", True))
    monkeypatch.setattr(blocks, "get_block", lambda *_args: {"block_id": "b"})
    monkeypatch.setattr(blocks, "update_block_text", lambda *_args: calls.append("update"))
    monkeypatch.setattr(blocks, "delete_block", lambda *_args: calls.append("delete-block"))
    monkeypatch.setattr(blocks, "batch_update_blocks", lambda *_args: {"ok": True})

    assert tools._action_get({"doc_token": "doc"}, CFG).success
    assert tools._action_delete({"doc_token": "doc"}, CFG).success
    assert tools._action_list_blocks({"doc_token": "doc", "page_token": "p"}, CFG).success
    assert tools._action_get_block({"doc_token": "doc", "block_id": "b"}, CFG).success
    assert tools._action_update_block(
        {"doc_token": "doc", "block_id": "b", "content": "x"}, CFG
    ).success
    assert tools._action_delete_block({"doc_token": "doc", "block_id": "b"}, CFG).success
    assert not tools._action_batch_update({"doc_token": "doc"}, CFG).success
    assert not tools._action_batch_update({"doc_token": "doc", "requests": "bad"}, CFG).success
    assert not tools._action_batch_update({"doc_token": "doc", "requests": "{}"}, CFG).success
    assert tools._action_batch_update(
        {"doc_token": "doc", "requests": json.dumps([{"x": 1}])}, CFG
    ).success
    assert calls == ["delete", "update", "delete-block"]


def test_import_export_and_plain_write(tmp_path, monkeypatch) -> None:
    import miniagent.assistant.feishu.docx.blocks as blocks
    import miniagent.assistant.feishu.docx.client as client

    monkeypatch.setattr(client, "get_document_raw_content", lambda *_args: "remote")
    monkeypatch.setattr(blocks, "append_plain_text_to_document", lambda *_args: 2)
    monkeypatch.setattr(blocks, "clear_document_content_blocks", lambda *_args: (3, 1))
    monkeypatch.setattr(tools, "_trace_docx_render", lambda *_args: None)

    context = ToolContext(cwd=str(tmp_path))
    exported = tools._action_export_raw(
        {"doc_token": "doc", "relative_path": "out.md"}, context, CFG
    )
    assert exported.success and (tmp_path / "out.md").read_text(encoding="utf-8") == "remote"
    assert not tools._action_export_raw(
        {"doc_token": "doc", "relative_path": "../escape"}, context, CFG
    ).success

    (tmp_path / "in.md").write_text("# title", encoding="utf-8")
    imported = tools._action_import_raw(
        {"doc_token": "doc", "relative_path": "in.md", "render_mode": "plain"}, context, CFG
    )
    assert imported.success and imported.meta["render_stats"]["written_blocks"] == 2
    written = tools._action_append(
        {"doc_token": "doc", "content": "text", "mode": "replace", "render_mode": "plain"},
        CFG,
        full_write=True,
    )
    assert written.success and "delete_failed=1" in written.content
    assert not tools._action_append({}, CFG, full_write=False).success
    assert not tools._action_append({"doc_token": "doc"}, CFG, full_write=False).success


def test_table_media_drive_and_permissions(tmp_path, monkeypatch) -> None:
    import miniagent.assistant.feishu.docx.media as media
    import miniagent.assistant.feishu.docx.tables as tables
    import miniagent.assistant.feishu.drive_extra as drive

    calls = []
    monkeypatch.setattr(tables, "create_table_block", lambda *_args, **_kw: "table")
    monkeypatch.setattr(tables, "write_table_cells", lambda *_args: calls.append("cells"))
    monkeypatch.setattr(tables, "create_table_with_values", lambda *_args, **_kw: "filled")
    monkeypatch.setattr(media, "upload_doc_image_from_path", lambda *_args: "image-token")
    monkeypatch.setattr(media, "upload_doc_file_from_path", lambda *_args: "file-token")
    monkeypatch.setattr(media, "download_media_bytes", lambda *_args, **_kw: b"data")
    monkeypatch.setattr(drive, "list_permissions", lambda *_args: [{"id": 1}])
    monkeypatch.setattr(drive, "add_permission", lambda *_args, **_kw: {"ok": True})
    monkeypatch.setattr(drive, "remove_permission", lambda *_args, **_kw: calls.append("remove"))

    context = ToolContext(cwd=str(tmp_path))
    (tmp_path / "image.png").write_bytes(b"image")
    (tmp_path / "file.bin").write_bytes(b"file")
    assert tools._action_create_table({"doc_token": "doc"}, CFG).success
    assert not tools._action_write_table_cells(
        {"doc_token": "doc", "table_block_id": "t", "values": "{}"}, CFG
    ).success
    assert tools._action_write_table_cells(
        {"doc_token": "doc", "table_block_id": "t", "values": '[[1, "x"]]'}, CFG
    ).success
    assert tools._action_create_table_with_values(
        {"doc_token": "doc", "values": '[["x"]]'}, CFG
    ).success
    assert tools._action_upload_image(
        {"doc_token": "doc", "relative_path": "image.png"}, context, CFG
    ).success
    assert tools._action_upload_file(
        {"doc_token": "doc", "relative_path": "file.bin"}, context, CFG
    ).success
    assert tools._action_download_media(
        {"file_token": "tok", "relative_path": "download.bin"}, context, CFG
    ).success
    assert (tmp_path / "download.bin").read_bytes() == b"data"
    assert tools._action_list_permissions({"doc_token": "doc"}, CFG).success
    assert tools._action_add_permission(
        {"doc_token": "doc", "member_type": "email", "email": "a@b.c"}, CFG
    ).success
    assert tools._action_remove_permission(
        {"doc_token": "doc", "member_type": "email", "email": "a@b.c"}, CFG
    ).success
    assert calls == ["cells", "remove"]


def test_copy_move_and_search_errors(monkeypatch) -> None:
    import miniagent.assistant.feishu.drive_extra as drive
    import miniagent.assistant.feishu.folder_token_resolve as folders

    monkeypatch.setattr(folders, "resolve_parent_folder_token", lambda *_args, **_kw: ("folder", None))
    monkeypatch.setattr(drive, "copy_file", lambda *_args, **_kw: "copy")
    monkeypatch.setattr(drive, "move_file", lambda *_args, **_kw: None)
    assert tools._action_copy({"doc_token": "doc", "folder_token": "folder"}, CFG).success
    assert tools._action_move({"doc_token": "doc", "folder_token": "folder"}, CFG).success

    monkeypatch.setattr(drive, "search_docs", lambda *_args: [{"token": "doc"}])
    assert tools._action_search({"query": "x"}, CFG).success
    monkeypatch.setattr(
        drive,
        "search_docs",
        lambda *_args: (_ for _ in ()).throw(drive.SearchRequiresUserTokenError("need token")),
    )
    assert not tools._action_search({"query": "x"}, CFG).success


@pytest.mark.asyncio
async def test_upload_image_from_message(monkeypatch) -> None:
    import miniagent.assistant.feishu.docx.media as media
    import miniagent.assistant.feishu.resource_io as resource_io

    async def download(*_args, **_kwargs):
        return b"image", "image/png"

    monkeypatch.setattr(resource_io, "download_message_resource", download)
    monkeypatch.setattr(media, "upload_doc_image_from_bytes", lambda *_args: "token")
    assert not (
        await tools._action_upload_image_from_message({}, ToolContext(cwd=""), CFG)
    ).success
    result = await tools._action_upload_image_from_message(
        {"doc_token": "doc", "message_id": "m", "file_key": "f"},
        ToolContext(cwd=""),
        CFG,
    )
    assert result.success and "token" in result.content
