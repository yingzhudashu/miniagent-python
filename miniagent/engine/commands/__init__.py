"""CLI 命令子包

从 cli_commands.py 拆分而来的独立命令模块，按功能职责分组：
- kb_commands: 知识库命令
- instance_commands: 实例管理命令
- config_commands: 环境配置检查函数

使用方式：
    from miniagent.engine.commands import cmd_kb_list, cmd_instance_handler
"""

from miniagent.engine.commands.kb_commands import (
    cmd_kb_list,
    cmd_kb_mount,
    cmd_kb_unmount,
    cmd_kb_search,
    cmd_kb_reload,
    format_kb_command_usage,
)
from miniagent.engine.commands.instance_commands import cmd_instance_handler
from miniagent.engine.commands.config_commands import (
    feishu_markdown_commands_enabled,
    feishu_dot_commands_full_enabled,
    format_test_command_usage,
)

__all__ = [
    # kb_commands
    "cmd_kb_list",
    "cmd_kb_mount",
    "cmd_kb_unmount",
    "cmd_kb_search",
    "cmd_kb_reload",
    "format_kb_command_usage",
    # instance_commands
    "cmd_instance_handler",
    # config_commands
    "feishu_markdown_commands_enabled",
    "feishu_dot_commands_full_enabled",
    "format_test_command_usage",
]