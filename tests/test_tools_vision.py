"""Tests for miniagent.assistant.tools.vision — image analysis tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.tools.vision import _analyze_image_handler
from tests.llm_helpers import MockGateway


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    """ToolContext sandboxed to tmp_path."""
    monkeypatch.chdir(tmp_path)
    return ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)])


async def test_analyze_image_file_not_found(ctx: ToolContext) -> None:
    """文件不存在应返回错误。"""
    r = await _analyze_image_handler({"path": "missing.png"}, ctx)
    assert not r.success
    assert "不存在" in r.content


async def test_analyze_image_path_escape(ctx: ToolContext, tmp_path: Path) -> None:
    """路径逃逸应返回沙箱错误。"""
    # 尝试访问沙箱外的路径
    r = await _analyze_image_handler({"path": "/etc/passwd"}, ctx)
    assert not r.success
    assert "越权" in r.content


async def test_analyze_image_file_too_large(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件过大应返回错误。"""
    # 创建一个大文件（模拟超过 20MB）
    img = tmp_path / "large.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (21 * 1024 * 1024))

    r = await _analyze_image_handler({"path": "large.png"}, ctx)
    assert not r.success
    assert "过大" in r.content


async def test_analyze_image_success(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """成功分析图片。"""
    # 创建假图片文件（仅用于路径检查，实际内容不重要）
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # PNG magic bytes

    # Mock OpenAI client
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content="这是一张测试图片")
                )
            ]
        )
    )

    monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-4o")

    ctx.llm_client = MockGateway(mock_client, vision=True)
    r = await _analyze_image_handler({"path": "test.png"}, ctx)

    assert r.success
    assert "测试图片" in r.content


async def test_analyze_image_custom_prompt(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自定义提示词应传递给 vision API。"""
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # JPEG magic bytes

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="图中文字：Hello"))]
        )
    )

    monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-4o")

    ctx.llm_client = MockGateway(mock_client, vision=True)
    r = await _analyze_image_handler(
        {"path": "test.jpg", "prompt": "识别图中的文字"}, ctx
    )

    assert r.success
    assert "Hello" in r.content


async def test_analyze_image_model_unsupported_vision(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型不支持视觉应返回错误。"""
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-3.5-turbo")

    mock_client = MagicMock()

    # describe_image 返回空字符串表示不支持
    ctx.llm_client = mock_client
    with patch(
        "miniagent.assistant.feishu.vision_desc.describe_image",
        new_callable=AsyncMock,
        return_value="",
    ):
        r = await _analyze_image_handler({"path": "test.png"}, ctx)

    assert not r.success
    assert "失败" in r.content


async def test_analyze_image_without_injected_client(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vision call without the application-owned client fails clearly."""
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-4o")
    r = await _analyze_image_handler({"path": "test.png"}, ctx)

    assert not r.success
    assert "未注入" in r.content


async def test_analyze_image_no_model(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置模型应返回错误。"""
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    mock_client = MagicMock()

    monkeypatch.delenv("MINIAGENT_MODEL_MODEL", raising=False)
    monkeypatch.delenv("MINIAGENT_MODEL_MODEL", raising=False)
    monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)

    ctx.llm_client = mock_client
    r = await _analyze_image_handler({"path": "test.png"}, ctx)

    assert not r.success
    assert "vision" in r.content.lower()


async def test_vision_tools_export() -> None:
    """vision_tools 应正确导出。"""
    from miniagent.assistant.tools.vision import vision_tools

    assert "analyze_image" in vision_tools
    tool = vision_tools["analyze_image"]
    assert tool.schema["function"]["name"] == "analyze_image"
    assert tool.permission == "sandbox"
    assert tool.toolbox == "vision"


async def test_vision_tools_in_all_tools() -> None:
    """vision_tools 应在 ALL_TOOLS 中。"""
    from miniagent.assistant.tools.registry import ALL_TOOLS

    assert "analyze_image" in ALL_TOOLS
