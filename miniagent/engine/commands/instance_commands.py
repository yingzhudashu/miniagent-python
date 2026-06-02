"""实例管理命令模块

提供 `.instance` 相关命令的实现：
- list: 列出所有运行中的实例
- stop: 停止指定实例

使用方式：
    from miniagent.engine.commands.instance_commands import cmd_instance_handler
"""

from __future__ import annotations

from typing import Any

from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def cmd_instance_handler(
    parts: list[str], sub_cmd: str, state: dict[str, Any], *, markdown: bool = False
) -> None:
    """处理 .instance 命令及其子命令。

    支持两个子命令：
    - list: 列出所有运行中的实例
    - stop <id>: 停止指定实例（不能停止当前实例）

    Args:
        parts: 命令分割后的参数列表
        sub_cmd: 子命令名称（list / stop）
        state: 运行时状态字典，包含 instance_id 等信息
        markdown: True 时实例列表为 GFM 表格（飞书 ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS``）
    """
    from miniagent.infrastructure.instance import (
        format_instances_markdown,
        format_instances_table,
        list_instances,
        stop_instance_by_id,
    )

    if sub_cmd == "list" or sub_cmd == "":
        # 列出所有运行中的实例
        instances = list_instances()
        if markdown:
            print(format_instances_markdown(instances))
        else:
            print(format_instances_table(instances))

    elif sub_cmd == "stop" and len(parts) >= 3:
        # 停止指定实例
        try:
            instance_id = int(parts[2])
        except ValueError:
            print(f"{WARNING_PREFIX} 无效的实例 ID: {parts[2]}")
            return

        my_instance_id = state.get("instance_id")
        if instance_id == my_instance_id:
            print(f"{WARNING_PREFIX} 不能停止当前实例，请使用 .stop")
            return

        result = stop_instance_by_id(instance_id)
        if result.get("success"):
            print(f"{SUCCESS_PREFIX} 实例 #{instance_id} 已停止: {result.get('reason', '')}")
        else:
            print(f"{ERROR_PREFIX} {result.get('message', '停止失败')}")

    else:
        print(f"{WARNING_PREFIX} 未知的子命令: {sub_cmd}")
        print("用法: .instance list | .instance stop <ID>")


__all__ = ["cmd_instance_handler"]