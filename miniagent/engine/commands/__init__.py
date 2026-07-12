"""CLI 命令子包

从 cli_commands.py 拆分而来的独立命令模块，按功能职责分组：
- kb_commands: 知识库命令
- instance_commands: 实例管理命令
- config_commands: 配置检查与命令用法辅助
- self_opt_commands: 自我优化提案的查询、审批、执行与报告

使用方式：
    from miniagent.engine.commands import cmd_kb_list, cmd_instance_handler
"""

from miniagent.engine.commands.config_commands import (
    feishu_dot_commands_full_enabled,
    feishu_markdown_commands_enabled,
    format_test_command_usage,
)
from miniagent.engine.commands.instance_commands import (
    cmd_instance_handler,
    format_instance_command_usage,
)
from miniagent.engine.commands.kb_commands import (
    cmd_kb_list,
    cmd_kb_mount,
    cmd_kb_reload,
    cmd_kb_search,
    cmd_kb_unmount,
    format_kb_command_usage,
)
from miniagent.engine.commands.self_opt_commands import (
    cmd_self_opt_analyze,
    cmd_self_opt_apply,
    cmd_self_opt_approve,
    cmd_self_opt_proposals,
    cmd_self_opt_reject,
    cmd_self_opt_report,
    cmd_self_opt_show,
    cmd_self_opt_status,
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
    "format_instance_command_usage",
    # config_commands
    "feishu_markdown_commands_enabled",
    "feishu_dot_commands_full_enabled",
    "format_test_command_usage",
    # self_opt_commands
    "cmd_self_opt_status",
    "cmd_self_opt_proposals",
    "cmd_self_opt_show",
    "cmd_self_opt_approve",
    "cmd_self_opt_reject",
    "cmd_self_opt_apply",
    "cmd_self_opt_analyze",
    "cmd_self_opt_report",
]
