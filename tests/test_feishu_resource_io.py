"""飞书资源下载辅助与 lark 响应错误格式化。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.feishu.lark_response import format_lark_response_error
from miniagent.feishu.resource_io import sanitize_filename


def test_sanitize_filename_strips_path_segments() -> None:
    assert sanitize_filename("../../etc/passwd", "fallback.bin") == ".._.._etc_passwd"
    assert sanitize_filename("", "empty.bin") == "empty.bin"
    assert sanitize_filename("plain.txt", "x.bin") == "plain.txt"


def test_format_lark_response_error_includes_code_and_msg() -> None:
    resp = MagicMock()
    resp.code = 99991663
    resp.msg = "invalid param"
    resp.log_id = "abc"
    s = format_lark_response_error(resp)
    assert "code=99991663" in s
    assert "invalid param" in s
    assert "log_id=abc" in s


@pytest.mark.asyncio
async def test_download_message_resource_success() -> None:
    pytest.importorskip("lark_oapi")
    from miniagent.feishu.resource_io import download_message_resource

    mock_file = MagicMock()
    mock_file.read.return_value = b"payload"
    ok = MagicMock()
    ok.success.return_value = True
    ok.file = mock_file
    ok.file_name = "doc.pdf"

    client = MagicMock()
    client.im.v1.message_resource.aget = AsyncMock(return_value=ok)
    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        data, name = await download_message_resource(
            "app",
            "secret",
            message_id="om_x",
            file_key="fk",
            type_="file",
        )
    assert data == b"payload"
    assert name == "doc.pdf"
