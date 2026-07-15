"""feishu_im_tools 处理器单测（仅 IM/云盘）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.config_helpers import install_test_config

pytest.importorskip("lark_oapi")


@pytest.mark.asyncio
async def test_feishu_list_drive_files_empty_table(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.agent.types.tool import ToolContext
    from miniagent.assistant.tools.feishu_im_tools import _feishu_list_drive_files

    monkeypatch.setenv("FEISHU_APP_ID", "a")
    monkeypatch.setenv("FEISHU_APP_SECRET", "b")
    install_test_config(tmp_path, {"feishu": {"doc": {"folder_token": "fld_env"}}})

    with patch(
        "miniagent.assistant.feishu.drive_client.list_folder_files_page",
        return_value=([], None, False),
    ):
        r = await _feishu_list_drive_files({}, ToolContext(cwd="/tmp"))
    assert r.success is True
    assert "has_more=False" in r.content
