"""CLI 命令处理模块

本模块包含所有 CLI 交互命令的实现，从 unified.py 拆分而来。

功能包括：
- 会话管理：列出、切换、创建、重命名会话
- 实例管理：列出运行中的实例、停止指定实例
- 消息队列：查看队列状态、切换队列模式
- 帮助显示：分类展示所有可用命令

注意：所有会话命令同时支持**编号**（如 1）和**原始 ID**（如 default）。
"""

from __future__ import annotations

import os
from typing import Any


def sync_channel_router_to_session(
    channel_router: Any,
    session_id: str,
    feishu_p2p_synced_senders: set[str] | None,
) -> None:
    """将 CLI 与「自动同步」的飞书私聊通道绑定到同一主会话，并更新 primary。"""
    from miniagent.infrastructure.channel_router import ChannelRouter

    if channel_router is None:
        return
    channel_router.bind(ChannelRouter.CLI_CHANNEL, session_id)
    channel_router.set_primary(session_id)
    if feishu_p2p_synced_senders:
        pfx = ChannelRouter.FEISHU_P2P_PREFIX
        for sid in feishu_p2p_synced_senders:
            channel_router.bind(f"{pfx}{sid}", session_id)


def _resolve_session(
    session_manager: Any, id_or_number: str
) -> str | None:
    """解析用户输入的会话标识（编号或原始 ID）。

    优先使用 SessionManager 内置的 resolve_session_id 方法，
    该方法支持内存和磁盘双重查找。如果不可用，则降级为
    手动遍历查找。

    Args:
        session_manager: 会话管理器实例
        id_or_number: 用户输入，如 "1"（编号）或 "default"（原始 ID）

    Returns:
        解析后的 session_id，找不到返回 None
    """
    # 优先使用 SessionManager 的内置解析方法
    if hasattr(session_manager, "resolve_session_id"):
        return session_manager.resolve_session_id(id_or_number)

    # 降级方案：纯数字按编号遍历查找
    if id_or_number.isdigit():
        num = int(id_or_number)
        for s in session_manager.list_all_sessions_with_info():
            if s["number"] == num:
                return s["id"]

    # 直接匹配 session_id
    if session_manager.get(id_or_number):
        return id_or_number

    return None


def cmd_session_list(session_manager: Any, active_session_id: str) -> None:
    """列出所有会话并标记当前活跃会话。

    显示每个会话的编号、标题、轮次和锁定状态。
    如果会话被其他实例锁定，会显示占用者的 PID。

    Args:
        session_manager: 会话管理器实例
        active_session_id: 当前活跃会话 ID
    """
    if not session_manager:
        print("⚠️ 会话管理器未初始化")
        return

    sessions = session_manager.list_all_sessions_with_info()
    my_pid = os.getpid()

    if not sessions:
        print("📭 暂无会话")
        return

    print("\n📋 会话列表:")
    for s in sessions:
        # 标记当前活跃会话
        marker = " ← 当前" if s["id"] == active_session_id else ""

        # 显示锁定信息
        lock_info = ""
        if s["locked"]:
            if s["lock_pid"] == my_pid:
                lock_info = " 🔒 (本实例)"
            else:
                lock_info = f" 🔒 (PID={s['lock_pid']})"

        display = f"#{s['number']} {s['title']}"
        print(f"  - {display}{marker} | {s['turn_count']} 轮{lock_info}")
    print()


def cmd_instance_handler(parts: list[str], sub_cmd: str, state: dict) -> None:
    """处理 .instance 命令及其子命令。

    支持两个子命令：
    - list: 列出所有运行中的实例
    - stop <id>: 停止指定实例（不能停止当前实例）

    Args:
        parts: 命令分割后的参数列表
        sub_cmd: 子命令名称（list / stop）
        state: 运行时状态字典，包含 instance_id 等信息
    """
    from miniagent.infrastructure.instance import (
        list_instances,
        stop_instance_by_id,
        format_instances_table,
    )

    if sub_cmd == "list" or sub_cmd == "":
        # 列出所有运行中的实例
        instances = list_instances()
        print(format_instances_table(instances))

    elif sub_cmd == "stop" and len(parts) >= 3:
        # 停止指定实例
        try:
            instance_id = int(parts[2])
        except ValueError:
            print(f"⚠️ 无效的实例 ID: {parts[2]}")
            return

        my_instance_id = state.get("instance_id")
        if instance_id == my_instance_id:
            print("⚠️ 不能停止当前实例，请使用 .stop")
            return

        result = stop_instance_by_id(instance_id)
        if result.get("success"):
            print(f"✅ 实例 #{instance_id} 已停止: {result.get('reason', '')}")
        else:
            print(f"❌ 停止失败: {result.get('reason', '')}")

    else:
        # 显示用法帮助
        print("\n用法:")
        print("  .instance list         列出所有运行实例")
        print("  .instance stop <id>    停止指定实例")
        print()


async def cmd_session_switch(
    session_manager: Any,
    active_session_id: str,
    id_or_number: str,
    try_lock_session: Any,
    release_session_lock: Any,
    is_session_locked: Any,
    channel_router: Any | None = None,
    feishu_p2p_synced_senders: set[str] | None = None,
) -> str:
    """切换到指定会话。

    流程：
    1. 解析会话标识（编号或 ID）
    2. 释放当前会话锁
    3. 检查目标会话是否被其他实例占用
    4. 获取目标会话锁
    5. 更新活跃会话 ID

    Args:
        session_manager: 会话管理器实例
        active_session_id: 当前活跃会话 ID
        id_or_number: 目标会话编号（如 1）或原始 ID
        try_lock_session: 尝试获取会话锁的函数
        release_session_lock: 释放会话锁的函数
        is_session_locked: 检查会话是否被锁定的函数

    Returns:
        新的活跃会话 ID（切换失败则返回原 ID）
    """
    if not session_manager:
        print("⚠️ 会话管理器未初始化")
        return active_session_id

    # 解析目标会话 ID
    session_id = _resolve_session(session_manager, id_or_number)
    if not session_id:
        print(f"❌ 会话不存在: {id_or_number}")
        return active_session_id

    # 释放当前会话锁
    release_session_lock(active_session_id)

    # 检查目标会话是否被其他实例锁定
    lock_pid = is_session_locked(session_id)
    if lock_pid is not None:
        # 尝试恢复会话（如果尚未加载）
        try:
            session_manager.get_or_create(session_id)
        except Exception:
            pass

        # 确认是否真的被锁定
        locked_sessions = [
            s
            for s in session_manager.list_all_sessions_with_info()
            if s["id"] == session_id and s["locked"]
        ]
        if locked_sessions:
            print(
                f"⚠️ 会话 #{locked_sessions[0]['number']} "
                f"{locked_sessions[0]['title']} 被其他实例占用 (PID={lock_pid})"
            )
            # 重新锁定当前会话
            try_lock_session(active_session_id)
            return active_session_id

    # 确保目标会话已加载
    try:
        session_manager.get_or_create(session_id)
    except Exception:
        pass

    # 获取目标会话锁
    ok, reason = try_lock_session(session_id)
    if not ok:
        print(f"❌ 无法切换: {reason}")
        try_lock_session(active_session_id)
        return active_session_id

    # 切换成功：CLI 与自动同步的飞书私聊跟到同一 session_key
    active_session_id = session_id
    sync_channel_router_to_session(
        channel_router, session_id, feishu_p2p_synced_senders
    )
    display = session_manager.get_session_display_name(session_id)
    print(f"🔄 已切换到会话: {display}")
    return active_session_id


async def cmd_session_create(
    session_manager: Any, session_id: str, title: str | None, try_lock_session: Any
) -> None:
    """创建新会话并自动获取锁。

    Args:
        session_manager: 会话管理器实例
        session_id: 新会话的唯一标识
        title: 会话标题（可选，默认为空）
        try_lock_session: 尝试获取会话锁的函数
    """
    if not session_manager:
        print("⚠️ 会话管理器未初始化")
        return

    from miniagent.session.manager import SessionOptions

    # 创建会话配置
    session_opts = SessionOptions(
        title=title or "",
        description=title or session_id,
    )
    session_manager.get_or_create(session_id, session_opts)

    # 获取新会话的锁
    try_lock_session(session_id)

    display = session_manager.get_session_display_name(session_id)
    print(f"✅ 已创建会话: {display}")


def cmd_session_rename(session_manager: Any, id_or_number: str, new_title: str) -> None:
    """重命名指定会话。

    Args:
        session_manager: 会话管理器实例
        id_or_number: 会话编号（如 1）或原始 ID
        new_title: 新的会话标题
    """
    if not session_manager:
        print("⚠️ 会话管理器未初始化")
        return

    session_id = _resolve_session(session_manager, id_or_number)
    if not session_id:
        print(f"❌ 会话不存在: {id_or_number}")
        return

    ok = session_manager.rename_session(session_id, new_title)
    if ok:
        display = session_manager.get_session_display_name(session_id)
        print(f"✅ 已重命名: {display}")
    else:
        print("❌ 重命名失败")


def cmd_queue_status(message_queue: Any) -> None:
    """查看消息队列状态。

    显示当前队列模式（queue / preemptive）以及
    每个聊天室的处理状态和等待消息数。

    Args:
        message_queue: 消息队列管理器实例
    """
    status = message_queue.get_status()
    mode_label = "🟢 队列模式" if status["mode"] == "queue" else "🔴 打断模式"
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
        print("✅ 已切换到队列模式（消息按顺序处理）")
    elif mode_str == "preemptive":
        message_queue.mode = QueueMode.PREEMPTIVE
        print("✅ 已切换到打断模式（最新消息打断前面处理）")
    else:
        print(f"❌ 未知模式: {mode_str}")
        print("   可用: queue, preemptive")


def cmd_bind(
    channel_router: Any, args: list[str], state: dict[str, Any] | None = None
) -> str:
    """绑定通道到指定会话。

    用法:
        .bind cli <会话>      将 CLI 通道绑定到指定会话
        .bind feishu <会话>   将飞书私聊绑定到指定会话（需 sender_id）
        .bind status          查看所有绑定状态

    Args:
        channel_router: ChannelRouter 实例
        args: 命令参数（如 ["cli", "oc_xxx"]）

    Returns:
        结果消息
    """
    from miniagent.infrastructure.channel_router import ChannelRouter

    if not args or args[0] == "status" or args[0] == "":
        return channel_router.status()

    if len(args) < 2:
        return (
            "用法:\n"
            "  .bind status              查看绑定状态\n"
            "  .bind cli <会话>          CLI 绑定到指定会话\n"
            "  .bind feishu <sender> <会话>  飞书私聊绑定（需 sender_id）"
        )

    channel = args[0].lower()

    if channel == "cli":
        session_id = args[1]
        old = channel_router.bind(ChannelRouter.CLI_CHANNEL, session_id)
        old_msg = f"（原绑定: {old}）" if old else ""
        return f"✅ CLI 已绑定到会话: {session_id} {old_msg}"

    elif channel == "feishu":
        if len(args) < 3:
            return "飞书私聊绑定需要 sender_id: .bind feishu <sender_id> <会话>"
        sender_id = args[1]
        session_id = args[2]
        channel_id = f"{ChannelRouter.FEISHU_P2P_PREFIX}{sender_id}"
        old = channel_router.bind(channel_id, session_id)
        old_msg = f"（原绑定: {old}）" if old else ""
        if state is not None:
            synced = state.setdefault("feishu_p2p_synced_senders", set())
            if isinstance(synced, set):
                synced.discard(sender_id)
        return f"✅ 飞书私聊 ({sender_id[:8]}...) 已绑定到: {session_id} {old_msg}"

    return f"❌ 未知通道: {channel}"


def cmd_unbind(
    channel_router: Any, args: list[str], state: dict[str, Any] | None = None
) -> str:
    """解除通道绑定。

    用法:
        .unbind cli       解除 CLI 绑定
        .unbind feishu <sender>  解除飞书私聊绑定
        .unbind all       解除所有绑定

    Args:
        channel_router: ChannelRouter 实例
        args: 命令参数

    Returns:
        结果消息
    """
    from miniagent.infrastructure.channel_router import ChannelRouter

    if not args or args[0] == "":
        return "用法: .unbind cli | .unbind feishu <sender> | .unbind all"

    target = args[0].lower()

    if target == "all":
        bindings = channel_router.get_all_bindings()
        if not bindings:
            return "📭 没有已绑定的通道"
        count = len(bindings)
        channel_router.unbind_all()
        if state is not None and "feishu_p2p_synced_senders" in state:
            st = state["feishu_p2p_synced_senders"]
            if isinstance(st, set):
                st.clear()
        return f"✅ 已解除 {count} 个通道绑定"

    elif target == "cli":
        old = channel_router.unbind(ChannelRouter.CLI_CHANNEL)
        if old:
            return f"✅ CLI 已解除绑定（原: {old}）"
        return "📭 CLI 未绑定任何会话"

    elif target == "feishu":
        if len(args) < 2:
            return "飞书私聊解绑需要 sender_id: .unbind feishu <sender_id>"
        sender_id = args[1]
        channel_id = f"{ChannelRouter.FEISHU_P2P_PREFIX}{sender_id}"
        old = channel_router.unbind(channel_id)
        if state is not None:
            synced = state.get("feishu_p2p_synced_senders")
            if isinstance(synced, set):
                synced.discard(sender_id)
        if old:
            return f"✅ 飞书私聊 ({sender_id[:8]}...) 已解除绑定（原: {old}）"
        return "📭 该飞书私聊未绑定任何会话"

    return f"❌ 未知通道: {target}"


def cmd_help(
    model_profiles: dict[str, Any],
    active_profile: str,
    message_queue: Any,
    instance_id: int | None = None,
) -> None:
    """显示分类帮助信息。

    按功能分组展示所有可用命令：
    - 启动命令
    - 实例管理
    - 会话管理
    - 飞书控制
    - 消息队列
    - 模型预设
    - 工具与统计
    - 实例控制
    - 其他命令

    Args:
        model_profiles: 可用的模型预设配置
        active_profile: 当前使用的模型预设
        message_queue: 消息队列管理器实例
        instance_id: 当前实例 ID（可选）
    """
    profiles = ", ".join(model_profiles.keys())
    mode = message_queue.mode.value
    inst_info = f" (#{instance_id})" if instance_id else ""

    print()
    print("  ╭─── Mini Agent 命令手册 ─────────────────────────────────╮")
    print()

    print("  🚀 启动命令（终端）")
    print("    python -m miniagent                     启动 CLI 模式")
    print("    python -m miniagent --feishu            启动 CLI + 飞书")
    print("    python -m miniagent --stop              列出实例；交互选择停止")
    print("    python -m miniagent --stop --all         停止全部实例")
    print("    python -m miniagent --stop <id>...       停止指定实例 ID")
    print()

    print("  🏭 实例管理")
    print("    .instance list                  列出所有运行实例")
    print("    .instance stop <id>             停止指定实例")
    print()

    print("  📁 会话管理")
    print("    .session list                   列出所有会话")
    print("    .session switch <编号/ID>       切换到指定会话")
    print("    .session create <ID> [标题]     创建新会话，可指定标题")
    print("    .session rename <编号/ID> <新标题>  重命名会话")
    print("  提示: 编号/ID 均可，如 .session switch 1 或 .session switch default")
    print()

    print("  💬 飞书控制")
    print("    .feishu start                   启动飞书 WebSocket 连接")
    print("    .feishu stop                    停止飞书连接")
    print("    .feishu status                  查看飞书运行状态")
    print()

    print("  📡 通道绑定")
    print("    .bind status                    查看通道绑定状态")
    print("    .bind cli <会话>                CLI 绑定到指定会话")
    print("    .bind feishu <sender> <会话>    飞书私聊绑定到指定会话")
    print("    .unbind cli                     解除 CLI 绑定")
    print("    .unbind feishu <sender>         解除飞书私聊绑定")
    print("    .unbind all                     解除所有绑定")
    print("  提示: 绑定后 CLI/飞书共享同一会话，记忆/文件/工具全部互通")
    print()

    print("  📬 消息队列")
    print("    .queue status                   查看队列状态")
    print("    .queue set <模式>               切换 queue / preemptive")
    print(f"    当前: {mode} (默认 queue)")
    print()

    print("  📡 模型预设")
    print("    .profile <名称>                 切换模型预设")
    print(f"    可用预设: {profiles}")
    print(f"    当前预设: {active_profile}")
    print()

    print("  📊 工具与统计")
    print("    .stats                          查看工具调用统计")
    print("    .status                         查看系统运行状态")
    print()

    print("  ⚙️ 实例控制")
    print(f"    .stop                           停止当前实例并退出{inst_info}")
    print()

    print("  📖 其他")
    print("    .help                           显示本帮助")
    print("    .copy                           复制当前会话 transcript 到剪贴板（全屏 CLI）")
    print("    quit / exit                     退出程序")
    print()

    print("  💡 提示: 直接输入文字即可与 Agent 对话")
    print("  ╰─────────────────────────────────────────────────────────╯")
    print()


__all__ = [
    "sync_channel_router_to_session",
    "cmd_session_list",
    "cmd_session_switch",
    "cmd_session_create",
    "cmd_session_rename",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
]
