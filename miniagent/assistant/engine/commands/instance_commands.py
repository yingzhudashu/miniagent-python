"""实例管理命令模块

提供 `/instance` 相关命令的实现：
- list: 列出所有运行中的实例
- stop: 停止指定实例

使用方式：
    from miniagent.assistant.engine.commands.instance_commands import cmd_instance_handler
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from contextlib import redirect_stdout
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


async def handle_instance(
    text: str,
    *,
    state: Mapping[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """解析实例子命令，并按渠道返回或打印叶子处理器输出。"""
    from miniagent.assistant.engine.commands.session_management import (
        feishu_markdown_commands_enabled,
    )

    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cmd_instance_handler(
            parts,
            subcommand,
            state,
            markdown=capture and feishu_markdown_commands_enabled(),
        )
    output = buffer.getvalue().strip()
    if capture:
        return output
    print(output)
    return None


def format_instance_command_usage() -> str:
    """返回 `/instance` 命令的用法说明。"""
    return (
        "实例管理命令：\n"
        "  /instance list              列出所有运行中的实例\n"
        "  /instance stop <ID>         停止指定实例（不可停止当前实例）"
    )


def cmd_instance_handler(
    parts: list[str], sub_cmd: str, state: Mapping[str, Any], *, markdown: bool = False
) -> None:
    """处理 `/instance` 命令及其子命令。

    支持两个子命令：
    - list: 列出所有运行中的实例
    - stop <id>: 停止指定实例（不能停止当前实例）

    通过 ``print`` 输出到 stdout；无返回值。飞书 capture 路径会捕获该输出。

    Args:
        parts: 命令分割后的参数列表（如 ``["/instance", "stop", "2"]``）
        sub_cmd: 子命令名称（``list`` / ``stop`` / 空字符串视为 list）
        state: 运行时状态字典，需含 ``instance_id``（int）以禁止停止自身
        markdown: True 时实例列表为 GFM 表格（由 ``feishu.markdown_commands`` 或
            ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS=1`` 启用）

    """
    from miniagent.assistant.infrastructure.instance import (
        format_instances_markdown,
        format_instances_table,
        list_instances,
        stop_instance_by_id,
    )

    if sub_cmd in ("list", ""):
        instances = list_instances()
        if markdown:
            print(format_instances_markdown(instances))
        else:
            print(format_instances_table(instances))

    elif sub_cmd == "stop" and len(parts) >= 3:
        try:
            instance_id = int(parts[2])
        except ValueError:
            print(f"{WARNING_PREFIX} 无效的实例 ID: {parts[2]}")
            return

        my_instance_id = state.get("instance_id")
        if instance_id == my_instance_id:
            print(f"{WARNING_PREFIX} 不能停止当前实例，请使用 /stop")
            return

        matches = [i for i in list_instances() if int(i.get("instance_id") or 0) == instance_id]
        if not matches:
            print(f"{ERROR_PREFIX} 实例 #{instance_id} 不存在")
            return
        result = stop_instance_by_id(instance_id, state_dir=str(matches[0].get("state_dir")))
        if result.get("success"):
            reason = result.get("reason") or ""
            if reason:
                print(f"{SUCCESS_PREFIX} 实例 #{instance_id} 已停止: {reason}")
            else:
                print(f"{SUCCESS_PREFIX} 实例 #{instance_id} 已停止")
        else:
            print(f"{ERROR_PREFIX} {result.get('reason', '停止失败')}")

    elif sub_cmd == "stop":
        print(f"{WARNING_PREFIX} 缺少实例 ID")
        print(format_instance_command_usage())

    else:
        if sub_cmd:
            print(f"{WARNING_PREFIX} 未知的子命令: {sub_cmd}")
        print(format_instance_command_usage())


__all__ = ["cmd_instance_handler", "format_instance_command_usage", "handle_instance"]
