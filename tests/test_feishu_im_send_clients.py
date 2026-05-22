"""``im_send`` / ``upload_io`` / ``docx_client`` 的 SDK mock 单测。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("lark_oapi")


@pytest.fixture(autouse=True)
def _clear_lark_client_cache():
    """每个测试前清除 Lark 客户端缓存，确保 mock 生效。"""
    from miniagent.feishu import im_send

    im_send.clear_client_cache()


def test_resolve_im_receive_id_type_env_and_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.im_send import resolve_im_receive_id_type

    assert resolve_im_receive_id_type("open_id") == "open_id"
    monkeypatch.setenv("MINIAGENT_FEISHU_RECEIVE_ID_TYPE", "union_id")
    assert resolve_im_receive_id_type(None) == "union_id"
    monkeypatch.delenv("MINIAGENT_FEISHU_RECEIVE_ID_TYPE", raising=False)
    assert resolve_im_receive_id_type(None) == "chat_id"


def test_post_im_message_reply_uses_reply_api() -> None:
    from miniagent.feishu.im_send import post_im_message
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    ok.data = MagicMock()
    ok.data.message_id = "mid_reply"
    client.im.v1.message.reply.return_value = ok
    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        success, mid, err = post_im_message(
            cfg,
            receive_id="oc_1",
            msg_type="text",
            content_json='{"text":"hi"}',
            reply_to_message_id="om_parent",
        )

    assert success and mid == "mid_reply" and err is None
    client.im.v1.message.reply.assert_called_once()
    client.im.v1.message.create.assert_not_called()


def test_post_im_message_create_returns_message_id() -> None:
    from miniagent.feishu.im_send import post_im_message
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    ok.data = MagicMock()
    ok.data.message_id = "mid_new"
    client.im.v1.message.create.return_value = ok

    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        success, mid, err = post_im_message(
            cfg,
            receive_id="oc_1",
            msg_type="file",
            content_json='{"file_key":"fk"}',
        )

    assert success and mid == "mid_new" and err is None
    client.im.v1.message.create.assert_called_once()


def test_post_im_message_create_uses_receive_id_type_open_id() -> None:
    from miniagent.feishu.im_send import post_im_message
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    ok.data = MagicMock()
    ok.data.message_id = "m2"
    client.im.v1.message.create.return_value = ok
    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        post_im_message(
            cfg,
            receive_id="ou_x",
            msg_type="text",
            content_json="{}",
            receive_id_type="open_id",
        )
    req = client.im.v1.message.create.call_args[0][0]
    assert getattr(req, "receive_id_type", None) == "open_id"


def test_list_folder_files_page_returns_entries() -> None:
    from miniagent.feishu.drive_client import list_folder_files_page
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    resp_ok = MagicMock()
    resp_ok.success.return_value = True
    f1 = MagicMock()
    f1.name = "A"
    f1.token = "tok1"
    f1.type = "folder"
    resp_ok.data = MagicMock()
    resp_ok.data.files = [f1]
    resp_ok.data.next_page_token = None
    resp_ok.data.has_more = False
    client.drive.v1.file.list.return_value = resp_ok
    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        entries, next_tok, more = list_folder_files_page(cfg, folder_token="fld")

    assert len(entries) == 1 and entries[0]["token"] == "tok1"
    assert next_tok is None and more is False


def test_append_plain_text_calls_create_children() -> None:
    from miniagent.feishu.docx.blocks import append_plain_text_to_document
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()

    list_ok = MagicMock()
    list_ok.success.return_value = True
    page = MagicMock()
    page.block_type = 1
    page.block_id = "page_root"
    list_ok.data = MagicMock()
    list_ok.data.items = [page]
    client.docx.v1.document_block.list.return_value = list_ok

    ch_ok = MagicMock()
    ch_ok.success.return_value = True
    ch_ok.data = MagicMock()
    ch_ok.data.items = []
    ch_ok.data.has_more = False
    ch_ok.data.page_token = None
    client.docx.v1.document_block_children.get.return_value = ch_ok

    create_ok = MagicMock()
    create_ok.success.return_value = True
    client.docx.v1.document_block_children.create.return_value = create_ok

    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        n = append_plain_text_to_document(cfg, "doc1", "hello\nworld")

    assert n == 2
    client.docx.v1.document_block_children.create.assert_called_once()


def test_send_im_image_message_success() -> None:
    from miniagent.feishu.types import FeishuConfig
    from miniagent.feishu.upload_io import send_im_image_message

    cfg = FeishuConfig(app_id="a", app_secret="b")
    with patch("miniagent.feishu.upload_io._post_im_message", return_value=(True, None)):
        ok, err = send_im_image_message(cfg, "oc_1", "img_key_1")
    assert ok is True and err is None


def test_delete_im_message_success() -> None:
    from miniagent.feishu.types import FeishuConfig
    from miniagent.feishu.upload_io import delete_im_message

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    client.im.v1.message.delete.return_value = ok
    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        success, err = delete_im_message(cfg, "om_del")
    assert success is True
    assert err == ""


def test_upload_im_file_uses_file_create() -> None:
    from miniagent.feishu.types import FeishuConfig
    from miniagent.feishu.upload_io import upload_im_file

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    ok.data = MagicMock()
    ok.data.file_key = "fk_out"
    client.im.v1.file.create.return_value = ok

    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        fk = upload_im_file(cfg, b"data", file_name="a.bin")

    assert fk == "fk_out"


def test_create_document_returns_ids() -> None:
    from miniagent.feishu.docx.client import create_document
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    doc = MagicMock()
    doc.document_id = "doc_9"
    doc.revision_id = 3
    ok.data = MagicMock()
    ok.data.document = doc
    client.docx.v1.document.create.return_value = ok

    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        did, rev = create_document(cfg, folder_token="fld", title="T")

    assert did == "doc_9" and rev == 3


def test_get_root_folder_meta_returns_token() -> None:
    from miniagent.feishu.drive_client import get_root_folder_meta
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    with (
        patch(
            "miniagent.feishu.drive_client._http_post_json",
            return_value={"code": 0, "tenant_access_token": "t-abc"},
        ),
        patch(
            "miniagent.feishu.drive_client._http_get_json",
            return_value={"code": 0, "data": {"token": "fld_root_meta"}},
        ),
    ):
        tok = get_root_folder_meta(cfg)
    assert tok == "fld_root_meta"


def test_get_root_folder_meta_raises_on_nonzero_code() -> None:
    from miniagent.feishu.drive_client import get_root_folder_meta
    from miniagent.feishu.types import FeishuConfig

    cfg = FeishuConfig(app_id="a", app_secret="b")
    with (
        patch(
            "miniagent.feishu.drive_client._http_post_json",
            return_value={"code": 0, "tenant_access_token": "t-abc"},
        ),
        patch(
            "miniagent.feishu.drive_client._http_get_json",
            return_value={"code": 91204, "msg": "FORBIDDEN"},
        ),
    ):
        with pytest.raises(RuntimeError, match="91204"):
            get_root_folder_meta(cfg)
