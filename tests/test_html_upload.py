"""Agent HTML 上传工具测试

测试 miniagent/tools/html_upload.py 的核心功能：
- HTML 内容上传
- 文件列表获取
- 过期文件清理
- 错误处理（网络、认证、大小限制）

设计背景见 docs/ARCHITECTURE.md § 工具层。
"""

from unittest.mock import patch

import pytest


class TestUploadHtmlHandler:
    """HTML 上传 Handler 测试"""

    @pytest.mark.asyncio
    async def test_upload_html_missing_html_param(self):
        """缺少 html 参数应返回错误"""
        from miniagent.tools.html_upload import _upload_html_handler
        from miniagent.types.tool import ToolContext

        ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])
        result = await _upload_html_handler({}, ctx)

        assert not result.success
        assert "缺少" in result.content

    @pytest.mark.asyncio
    async def test_upload_html_missing_api_key(self):
        """未配置 API Key 应返回错误"""
        from miniagent.tools.html_upload import _upload_html_handler
        from miniagent.types.tool import ToolContext

        ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

        with patch("miniagent.tools.html_upload._get_api_key", return_value=None):
            result = await _upload_html_handler({"html": "<html></html>"}, ctx)

            assert not result.success
            assert "api_key" in result.content.lower()

    @pytest.mark.asyncio
    async def test_upload_html_size_exceeded(self):
        """超过大小限制应返回错误"""
        from miniagent.tools.html_upload import _upload_html_handler
        from miniagent.types.tool import ToolContext

        ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

        # 创建超过 2MB 的内容
        large_html = "x" * (3 * 1024 * 1024)

        with patch("miniagent.tools.html_upload._get_api_key", return_value="test_key"):
            result = await _upload_html_handler({"html": large_html}, ctx)

            assert not result.success


class TestListHtmlFilesHandler:
    """文件列表 Handler 测试"""

    @pytest.mark.asyncio
    async def test_list_html_files_missing_api_key(self):
        """未配置 API Key 应返回错误"""
        from miniagent.tools.html_upload import _list_html_files_handler
        from miniagent.types.tool import ToolContext

        ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

        with patch("miniagent.tools.html_upload._get_api_key", return_value=None):
            result = await _list_html_files_handler({}, ctx)

            assert not result.success


class TestCleanupHtmlFilesHandler:
    """清理文件 Handler 测试"""

    @pytest.mark.asyncio
    async def test_cleanup_missing_api_key(self):
        """未配置 API Key 应返回错误"""
        from miniagent.tools.html_upload import _cleanup_html_files_handler
        from miniagent.types.tool import ToolContext

        ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

        with patch("miniagent.tools.html_upload._get_api_key", return_value=None):
            result = await _cleanup_html_files_handler({}, ctx)

            assert not result.success


class TestToolDefinitions:
    """工具定义测试"""

    def test_upload_html_tool_exists(self):
        """验证工具定义存在"""
        from miniagent.tools.html_upload import upload_html_tool

        assert upload_html_tool is not None
        assert upload_html_tool.schema is not None

    def test_list_html_files_tool_exists(self):
        """验证工具定义存在"""
        from miniagent.tools.html_upload import list_html_files_tool

        assert list_html_files_tool is not None

    def test_cleanup_html_files_tool_exists(self):
        """验证工具定义存在"""
        from miniagent.tools.html_upload import cleanup_html_files_tool

        assert cleanup_html_files_tool is not None


class TestConfigFunctions:
    """配置函数测试"""

    def test_get_api_key_returns_none_by_default(self):
        """默认返回 None"""
        from miniagent.tools.html_upload import _get_api_key

        with patch("miniagent.tools.html_upload.get_config", return_value=None):
            result = _get_api_key()
            assert result is None

    def test_get_base_url_returns_config_value(self):
        """返回配置值或默认值"""
        from miniagent.tools.html_upload import DEFAULT_BASE_URL, _get_base_url

        # 测试默认值
        with patch("miniagent.tools.html_upload.get_config", side_effect=lambda k, d: d):
            result = _get_base_url()
            assert result == DEFAULT_BASE_URL

    def test_get_max_size_returns_config_value(self):
        """返回配置值或默认值"""
        from miniagent.tools.html_upload import DEFAULT_MAX_SIZE, _get_max_size

        # 测试默认值
        with patch("miniagent.tools.html_upload.get_config", side_effect=lambda k, d: d):
            result = _get_max_size()
            assert result == DEFAULT_MAX_SIZE