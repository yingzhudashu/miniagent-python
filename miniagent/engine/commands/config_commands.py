"""配置与环境检查命令模块

提供飞书命令开关检查函数：
- feishu_markdown_commands_enabled: 飞书是否使用 Markdown 表格输出
- feishu_dot_commands_full_enabled: 飞书是否允许全量点命令

使用方式：
    from miniagent.engine.commands.config_commands import feishu_dot_commands_full_enabled
"""

from __future__ import annotations

from miniagent.infrastructure.json_config import get_config


def feishu_markdown_commands_enabled() -> bool:
    """飞书 capture 路径下是否用 Markdown 表格输出部分 `.` 命令（会话列表、队列、实例列表）。"""
    return bool(get_config("feishu.markdown_commands", False))


def feishu_dot_commands_full_enabled() -> bool:
    """飞书是否允许与 CLI 相同的命令（含 /session/.schedule 变异与 /stop）。

    开启方式（任一即可）：
    - 环境变量 ``MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1``
    - 配置 ``feishu.dot_commands_full: true``（config.user.json）
    """
    from miniagent.infrastructure.env_parse import env_flag

    return env_flag("MINIAGENT_FEISHU_DOT_COMMANDS_FULL") or bool(
        get_config("feishu.dot_commands_full", False)
    )


def format_test_command_usage() -> str:
    """返回 `/test` 自测命令的用法说明。"""
    return (
        "自测命令（运行预设测试用例，默认 mock 模式）：\n"
        "  /test list                              列出可用测试用例\n"
        "  /test run                               运行所有测试\n"
        "  /test run <类别>                        按类别过滤\n"
        "  /test run <类别> <名称>                 按名称正则进一步过滤\n"
        "  /test status                            查看最近测试结果\n"
        "  说明: 测试样本位于 tests/evaluation/samples/"
    )


__all__ = [
    "feishu_markdown_commands_enabled",
    "feishu_dot_commands_full_enabled",
    "format_test_command_usage",
]
