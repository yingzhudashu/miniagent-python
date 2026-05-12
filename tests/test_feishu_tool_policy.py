"""飞书内置工具注册策略、通道 system 提示与建文档 URL 后缀。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_feishu_im_tools_explicit_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS", "1")
    monkeypatch.delenv("MINIAGENT_FEISHU_TOOLS_AUTO", raising=False)
    from miniagent.feishu.im_tool_policy import feishu_im_tools_should_register

    assert feishu_im_tools_should_register() is True


def test_feishu_im_tools_explicit_off_overrides_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS", "0")
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS_AUTO", "1")
    from miniagent.feishu.im_tool_policy import feishu_im_tools_should_register

    assert feishu_im_tools_should_register() is False


def test_feishu_im_tools_auto_when_unset_tools_and_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_TOOLS", raising=False)
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS_AUTO", "1")
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    from miniagent.feishu.im_tool_policy import feishu_im_tools_should_register

    assert feishu_im_tools_should_register() is True


def test_feishu_im_tools_garbage_value_does_not_fall_through_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS", "maybe")
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS_AUTO", "1")
    from miniagent.feishu.im_tool_policy import feishu_im_tools_should_register

    assert feishu_im_tools_should_register() is False


def test_feishu_im_tools_auto_off_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_TOOLS", raising=False)
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS_AUTO", "1")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    from miniagent.feishu.im_tool_policy import feishu_im_tools_should_register

    assert feishu_im_tools_should_register() is False


def test_startup_hint_logs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.setenv("MINIAGENT_FEISHU_TOOLS", "0")
    from miniagent.feishu import im_tool_policy as pol

    pol.reset_feishu_im_tools_startup_hint_for_tests()
    with patch.object(pol._logger, "info") as mock_info:
        pol.log_feishu_im_tools_startup_hint_once()
        pol.log_feishu_im_tools_startup_hint_once()
    assert mock_info.call_count == 1


def test_append_feishu_channel_session_registry_without_tools_differs_from_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """与 execute_plan 一致：effective_registry 为 session 时，以会话表为准决定 system 段。"""
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

    main = MagicMock()

    def main_get(name: str):
        return MagicMock() if name == "feishu_create_document" else None

    main.get = main_get
    sess = MagicMock()
    sess.get = MagicMock(return_value=None)
    out_sess = append_feishu_channel_system("base", is_feishu=True, registry=sess)
    out_main = append_feishu_channel_system("base", is_feishu=True, registry=main)
    assert "未注册" in (out_sess or "")
    assert "实际调用工具" in (out_main or "")


def test_append_feishu_channel_with_tools() -> None:
    reg = MagicMock()

    def _get(name: str):
        return {"x": 1} if name == "feishu_create_document" else None

    reg.get = _get
    from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

    out = append_feishu_channel_system("base", is_feishu=True, registry=reg)
    assert out is not None
    assert "base" in out
    assert "feishu_create_document" in out


def test_append_feishu_channel_without_tools_when_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    reg = MagicMock()
    reg.get = MagicMock(return_value=None)
    from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

    out = append_feishu_channel_system(None, is_feishu=True, registry=reg)
    assert out is not None
    assert "MINIAGENT_FEISHU_TOOLS" in out


def test_append_feishu_channel_skips_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    reg = MagicMock()
    reg.get = MagicMock(return_value=None)
    from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

    assert append_feishu_channel_system("only", is_feishu=True, registry=reg) == "only"


@pytest.mark.asyncio
async def test_feishu_create_document_accepts_folder_share_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.delenv("FEISHU_DEFAULT_DOC_FOLDER_TOKEN", raising=False)
    ctx = MagicMock()
    url = "https://tenant.feishu.cn/drive/folder/fldcnFromShare"
    with patch("miniagent.feishu.docx_client.create_document") as mock_create:
        mock_create.return_value = ("doc_y", 1)
        from miniagent.tools.feishu_im_tools import _feishu_create_document

        r = await _feishu_create_document({"title": "T", "folder_token": url}, ctx)
    assert r.success
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["folder_token"] == "fldcnFromShare"


@pytest.mark.asyncio
async def test_feishu_create_document_includes_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.setenv("FEISHU_DEFAULT_DOC_FOLDER_TOKEN", "fld")
    monkeypatch.setenv("FEISHU_DOCX_URL_PREFIX", "https://example.feishu.cn/docx/")
    ctx = MagicMock()
    with patch("miniagent.feishu.docx_client.create_document", return_value=("doc_x", 1)):
        from miniagent.tools.feishu_im_tools import _feishu_create_document

        r = await _feishu_create_document({"title": "Hi"}, ctx)
    assert r.success
    assert "https://example.feishu.cn/docx/doc_x" in r.content
