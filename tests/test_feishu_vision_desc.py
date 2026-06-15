"""``vision_desc.describe_image`` 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.feishu import vision_desc


@pytest.mark.asyncio
async def test_describe_image_success(tmp_path: Path) -> None:
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="  一张示意图  "))]
        )
    )

    out = await vision_desc.describe_image(str(img), client, "gpt-4o")
    assert out == "一张示意图"
    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_describe_image_unsupported_model_returns_empty(tmp_path: Path) -> None:
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("invalid_request_error: does not support image_url")
    )

    out = await vision_desc.describe_image(str(img), client, "text-only")
    assert out == ""


@pytest.mark.asyncio
async def test_describe_image_skips_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img = tmp_path / "big.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    monkeypatch.setattr(vision_desc, "_max_image_bytes", lambda: 10)

    client = MagicMock()
    out = await vision_desc.describe_image(str(img), client, "gpt-4o")
    assert out == ""
    client.chat.completions.create.assert_not_called()
