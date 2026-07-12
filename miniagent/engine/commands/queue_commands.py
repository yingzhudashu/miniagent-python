"""消息队列命令的展示与模式切换叶子函数。"""

from __future__ import annotations

from typing import Any

from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX


def _md_escape_cell(text: str) -> str:
    """转义 GFM 表格单元格中的分隔符与换行。"""
    return str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

def format_queue_command_usage(message_queue: Any) -> str:
    """与帮助中队列小节一致的用法说明。"""
    mode = message_queue.mode.value
    return (
        "用法:\n"
        "  /queue status                   查看队列状态\n"
        "  /queue set <模式>               切换 queue / preemptive\n"
        "  /queue abort                    中止本通道队列（含 dispatch_wait 投递中的任务；不退出进程）\n"
        "  /abort                          同上（短命令）\n"
        f"  当前模式: {mode}"
    )


def format_queue_abort_message(result: dict[str, Any]) -> str:
    """将 :meth:`~miniagent.infrastructure.message_queue.MessageQueueManager.abort_chat` 的返回值格式化为用户可读文案。"""
    cr = bool(result.get("cancelled_running"))
    cp = int(result.get("cancelled_pending") or 0)
    pr = bool(result.get("cancelled_preemptive_current"))
    cdw = int(result.get("cancelled_dispatch_wait") or 0)
    if not cr and cp == 0 and not pr and cdw == 0:
        return (
            f"{SUCCESS_PREFIX} 已处理：当前聊天队列无运行中或排队的任务（进程与实例仍在运行）。\n"
            "提示：全屏 CLI 在 Agent 单轮执行期间无法再次输入命令；飞书侧可随时发送 `/abort` / `/queue abort` 打断。"
        )
    lines: list[str] = [
        f"{SUCCESS_PREFIX} 已中止本聊天消息队列上的任务（未调用 `/stop`，进程与实例仍在运行）。",
    ]
    if pr:
        lines.append("  · 已取消打断（preemptive）模式下当前执行的任务。")
    if cr and not pr:
        lines.append("  · 已取消正在执行的任务。")
    if cp > 0:
        lines.append(f"  · 已取消 {cp} 个排队中的任务。")
    if cdw > 0:
        lines.append(f"  · 已取消 {cdw} 个 dispatch_wait 包装任务（如经该路径投递的定时回合）。")
    return "\n".join(lines)


def cmd_queue_status(message_queue: Any, *, markdown: bool = False) -> None:
    """查看消息队列状态。

    显示当前队列模式（queue / preemptive）以及
    每个聊天室的处理状态和等待消息数。

    Args:
        message_queue: 消息队列管理器实例
        markdown: True 时输出 GFM 表格（由 ``feishu.markdown_commands`` 或
            ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS=1`` 启用）
    """
    status = message_queue.get_status()
    mode_label = "🟢 队列模式" if status["mode"] == "queue" else "🔴 打断模式"

    if markdown:
        lines = [
            "## 消息队列状态",
            "",
            f"**模式**: {mode_label}（`{status['mode']}`）",
            "",
            "| 会话 | 状态 | 等待条数 |",
            "| --- | --- | --- |",
        ]
        for label, info in status["chats"].items():
            busy = "处理中" if info["busy"] else "空闲"
            pend = str(info["pending"])
            lines.append(f"| {_md_escape_cell(label)} | {busy} | {pend} |")
        print("\n".join(lines))
        print()
        return

    print("\n📬 消息队列状态:")
    print(f"  模式: {mode_label} ({status['mode']})")

    for label, info in status["chats"].items():
        busy_icon = "🔴" if info["busy"] else "⚪"
        if info["busy"]:
            print(f"  {label}: {busy_icon} 处理中")
        else:
            print(f"  {label}: 空闲")

        if info["pending"] > 0:
            print(f"    等待: {info['pending']} 条")
    print()


async def cmd_queue_set(message_queue: Any, mode_str: str) -> None:
    """切换消息队列处理模式。

    Args:
        message_queue: 消息队列管理器实例
        mode_str: 目标模式名称（queue / preemptive）
    """
    from miniagent.infrastructure.message_queue import QueueMode

    mode_str = mode_str.lower()
    if mode_str == "queue":
        message_queue.mode = QueueMode.QUEUE
        print(f"{SUCCESS_PREFIX} 已切换到队列模式（消息按顺序处理）")
    elif mode_str == "preemptive":
        message_queue.mode = QueueMode.PREEMPTIVE
        print(f"{SUCCESS_PREFIX} 已切换到打断模式（最新消息打断前面处理）")
    else:
        print(f"{ERROR_PREFIX} 未知模式: {mode_str}")
        print("   可用: queue, preemptive")


__all__ = [
    "cmd_queue_set",
    "cmd_queue_status",
    "format_queue_abort_message",
    "format_queue_command_usage",
]
