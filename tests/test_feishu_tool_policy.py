"""飞书内置工具注册策略、通道 system 提示。"""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock, patch

import pytest

# Check if lark-oapi is available
_HAS_LARK_OAPI = importlib.util.find_spec("lark_oapi") is not None


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


def test_feishu_ext_tool_names_includes_doc_and_bitable() -> None:
    from miniagent.feishu.feishu_tool_policy import FEISHU_EXT_TOOL_NAMES

    assert "feishu_doc" in FEISHU_EXT_TOOL_NAMES
    assert "feishu_bitable" in FEISHU_EXT_TOOL_NAMES
    assert "feishu_send_workspace_file" in FEISHU_EXT_TOOL_NAMES
    assert "feishu_create_document" not in FEISHU_EXT_TOOL_NAMES
    assert "feishu_send_interactive_card" in FEISHU_EXT_TOOL_NAMES
    assert "feishu_update_message_card" in FEISHU_EXT_TOOL_NAMES


def test_append_feishu_channel_with_feishu_doc() -> None:
    reg = MagicMock()

    def _get(name: str):
        return {"x": 1} if name == "feishu_doc" else None

    reg.get = _get
    from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

    out = append_feishu_channel_system("base", is_feishu=True, registry=reg)
    assert out is not None
    assert "feishu_doc" in out
    assert "feishu_bitable" in out


def test_append_feishu_channel_without_tools_when_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    reg = MagicMock()
    reg.get = MagicMock(return_value=None)
    from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

    out = append_feishu_channel_system(None, is_feishu=True, registry=reg)
    assert out is not None
    assert "MINIAGENT_FEISHU_TOOLS" in out


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAS_LARK_OAPI, reason="lark-oapi not installed (feishu extra)")
async def test_feishu_doc_create_accepts_folder_share_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    monkeypatch.delenv("MINIAGENT_FEISHU_DOC_FOLDER_TOKEN", raising=False)
    monkeypatch.setenv("FEISHU_DOC_FOLDER_FALLBACK_ROOT_META", "0")
    url = "https://tenant.feishu.cn/drive/folder/fldcnFromShare"

    with patch("miniagent.feishu.docx.client.create_document", return_value=("doc_y", 1)):
        from miniagent.tools.feishu_doc_tools import _feishu_doc
        from miniagent.types.tool import ToolContext

        r = await _feishu_doc(
            {"action": "create", "title": "T", "folder_token": url}, ToolContext(cwd="/tmp")
        )
    assert r.success
