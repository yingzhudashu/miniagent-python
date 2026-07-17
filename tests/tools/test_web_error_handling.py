"""测试时间和应用可用性检查工具 — core_tools.py 和 skills.py 的工具处理逻辑。

覆盖 get_time、check_app_availability 的错误处理和边界情况。

重构说明：web.py 已重命名为 core_tools.py，check_app_availability 已合并到 skills.py。
"""

from unittest.mock import patch

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.tools.core_tools import core_tools
from miniagent.assistant.tools.skills import skills_tools


@pytest.mark.asyncio
async def test_get_time_default_timezone():
    """测试默认时区获取时间。"""
    tool_def = core_tools["get_time"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    result = await tool_def.handler({}, ctx)

    assert result.success is True
    assert "当前时间" in result.content
    assert "ISO:" in result.content


@pytest.mark.asyncio
async def test_get_time_specific_timezone():
    """测试指定时区获取时间。"""
    tool_def = core_tools["get_time"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    result = await tool_def.handler({"timezone": "Asia/Shanghai"}, ctx)

    assert result.success is True
    assert "Asia/Shanghai" in result.content or "当前时间" in result.content


@pytest.mark.asyncio
async def test_get_time_invalid_timezone():
    """测试无效时区处理（应回退到UTC）。"""
    tool_def = core_tools["get_time"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    result = await tool_def.handler({"timezone": "Invalid/Zone"}, ctx)

    assert result.success is True
    # 应回退到UTC或系统时区
    assert "当前时间" in result.content


@pytest.mark.asyncio
async def test_check_binary_available():
    """测试检查可用的命令行工具。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    with patch('shutil.which', return_value='/usr/bin/python'):
        result = await tool_def.handler({"type": "binary", "name": "python"}, ctx)

    assert result.success is True
    assert "python" in result.content
    assert "可用" in result.content


@pytest.mark.asyncio
async def test_check_binary_not_found():
    """测试检查不可用的命令行工具。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    with patch('shutil.which', return_value=None):
        result = await tool_def.handler({"type": "binary", "name": "nonexistent_cmd"}, ctx)

    assert result.success is False
    assert "不可用" in result.content or "未找到" in result.content


@pytest.mark.asyncio
async def test_check_env_available():
    """测试检查存在的环境变量。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    with patch.dict('os.environ', {'TEST_VAR': 'test_value_12345'}):
        result = await tool_def.handler({"type": "env", "name": "TEST_VAR"}, ctx)

    assert result.success is True
    assert "TEST_VAR" in result.content
    assert "可用" in result.content or "已设置" in result.content


@pytest.mark.asyncio
async def test_check_env_not_found():
    """测试检查不存在环境变量。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    # 确保环境变量不存在
    import os
    if 'NONEXISTENT_VAR' in os.environ:
        del os.environ['NONEXISTENT_VAR']

    result = await tool_def.handler({"type": "env", "name": "NONEXISTENT_VAR"}, ctx)

    assert result.success is False
    assert "未设置" in result.content or "不可用" in result.content


@pytest.mark.asyncio
async def test_check_python_available():
    """测试检查已安装的Python包。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    # pytest应该已安装（我们正在使用它）
    result = await tool_def.handler({"type": "python", "name": "pytest"}, ctx)

    assert result.success is True
    assert "pytest" in result.content
    assert "可用" in result.content


@pytest.mark.asyncio
async def test_check_python_not_found():
    """测试检查未安装的Python包。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    # 使用一个不太可能安装的包名
    result = await tool_def.handler({"type": "python", "name": "nonexistent_package_xyz"}, ctx)

    assert result.success is False
    assert "未安装" in result.content or "不可用" in result.content


@pytest.mark.asyncio
async def test_check_empty_name():
    """测试空名称参数错误处理。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    result = await tool_def.handler({"type": "binary", "name": ""}, ctx)

    assert result.success is False
    assert "不能为空" in result.content or "ERROR" in result.content


@pytest.mark.asyncio
async def test_check_invalid_type():
    """测试无效检查类型错误处理。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    result = await tool_def.handler({"type": "invalid_type", "name": "test"}, ctx)

    assert result.success is False
    assert "不支持的检查类型" in result.content or "invalid_type" in result.content


@pytest.mark.asyncio
async def test_check_com_non_windows():
    """测试非Windows平台COM检查错误处理。"""
    tool_def = skills_tools["check_app_availability"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    with patch('os.name', 'posix'):
        result = await tool_def.handler({"type": "com", "name": "Test.Application"}, ctx)

    assert result.success is False
    assert "仅支持 Windows" in result.content or "不可用" in result.content


@pytest.mark.asyncio
async def test_get_time_weekday_format():
    """测试时间包含星期格式。"""
    tool_def = core_tools["get_time"]
    ctx = ToolContext(cwd="/tmp", allowed_paths=["/tmp"])

    result = await tool_def.handler({}, ctx)

    # 应包含星期几
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    assert any(wd in result.content for wd in weekdays)


def test_core_and_skills_tools_schema():
    """测试工具 schema 结构正确性。"""
    assert "get_time" in core_tools
    assert "check_app_availability" in skills_tools

    # 检查 schema 结构
    get_time_def = core_tools["get_time"]
    assert get_time_def.schema["type"] == "function"
    assert get_time_def.schema["function"]["name"] == "get_time"

    check_app_def = skills_tools["check_app_availability"]
    assert check_app_def.schema["type"] == "function"
    assert check_app_def.permission == "sandbox"


def test_core_tools_permission():
    """测试工具权限配置正确性。"""
    for tool_def in core_tools.values():
        assert tool_def.permission == "sandbox"
        assert tool_def.toolbox == "core"
