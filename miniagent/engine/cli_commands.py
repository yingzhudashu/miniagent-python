"""CLI 命令处理模块

本模块包含所有 CLI 交互命令的实现，从 unified.py 拆分而来。

功能包括：
- 会话管理：列出、切换、创建、重命名会话
- 实例管理：列出运行中的实例、停止指定实例
- 消息队列：查看队列状态、切换队列模式
- 帮助显示：分类展示所有可用命令

注意：所有会话命令同时支持**编号**（如 1）和**原始 ID**（如 default）。

终端帮助正文与表格格式维护请与 ``docs/CLI.md`` 对齐。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


def feishu_markdown_commands_enabled() -> bool:
    """飞书 capture 路径下是否用 Markdown 表格输出部分 `.` 命令（会话列表、队列、实例列表）。"""
    v = os.environ.get("MINIAGENT_FEISHU_MARKDOWN_COMMANDS", "0")
    return str(v).strip().lower() in ("1", "true", "yes")


def format_session_command_usage() -> str:
    """与 ``format_help_markdown`` 中会话小节一致的用法说明（CLI 提示与 dispatch 共用）。"""
    return (
        "用法:\n"
        "  .session list                   列出所有会话\n"
        "  .session switch <编号/ID>       切换到指定会话（飞书 capture 下仅 list；改会话请在本地 CLI）\n"
        "  .session create <ID> [标题]     创建新会话\n"
        "  .session rename <编号/ID> <标题>  重命名会话"
    )


def format_queue_command_usage(message_queue: Any) -> str:
    """与帮助中队列小节一致的用法说明。"""
    mode = message_queue.mode.value
    return (
        "用法:\n"
        "  .queue status                   查看队列状态\n"
        "  .queue set <模式>               切换 queue / preemptive\n"
        "  .queue abort                    中止本通道队列（含 dispatch_wait 投递中的任务；不退出进程）\n"
        "  .abort                          同上（短命令）\n"
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
            "✅ 已处理：当前聊天队列无运行中或排队的任务（进程与实例仍在运行）。\n"
            "提示：全屏 CLI 在 Agent 单轮执行期间无法再次输入点命令；飞书侧可随时发送 `.abort` / `.queue abort` 打断。"
        )
    lines: list[str] = [
        "✅ 已中止本聊天消息队列上的任务（未调用 `.stop`，进程与实例仍在运行）。",
    ]
    if pr:
        lines.append("  · 已取消打断（preemptive）模式下当前执行的任务。")
    if cr and not pr:
        lines.append("  · 已取消正在执行的任务。")
    if cp > 0:
        lines.append(f"  · 已取消 {cp} 个排队中的任务。")
    if cdw > 0:
        lines.append(
            f"  · 已取消 {cdw} 个 dispatch_wait 包装任务（如经该路径投递的定时回合）。"
        )
    return "\n".join(lines)


def _md_escape_cell(text: str) -> str:
    """表格单元格：去掉换行并转义管道符。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|").replace("\n", " ").strip()
    return s


def _md_help_section(title: str, hint: str | None, rows: list[tuple[str, str]]) -> str:
    """生成分组 Markdown：可选引用提示 + GFM 表格。"""
    lines: list[str] = [f"### {title}", ""]
    if hint:
        lines.append(f"> {hint}")
        lines.append("")
    lines.extend(["| 命令 | 说明 |", "| --- | --- |"])
    for cmd, desc in rows:
        lines.append(f"| {_md_escape_cell(cmd)} | {_md_escape_cell(desc)} |")
    lines.append("")
    return "\n".join(lines)


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


def cmd_session_list(
    session_manager: Any, active_session_id: str, *, markdown: bool = False
) -> None:
    """列出所有会话并标记当前活跃会话。

    显示每个会话的编号、标题、轮次和锁定状态。
    如果会话被其他实例锁定，会显示占用者的 PID。

    Args:
        session_manager: 会话管理器实例
        active_session_id: 当前活跃会话 ID
        markdown: True 时输出 GFM 表格（飞书 ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS``）
    """
    if not session_manager:
        print("⚠️ 会话管理器未初始化")
        return

    sessions = session_manager.list_all_sessions_with_info()
    my_pid = os.getpid()

    if not sessions:
        print("📭 暂无会话")
        return

    if markdown:
        lines = ["## 会话列表", "", "| 编号 | 会话 | 轮次 | 备注 |", "| --- | --- | --- | --- |"]
        for s in sessions:
            marker = "当前" if s["id"] == active_session_id else ""
            lock_info = ""
            if s["locked"]:
                if s["lock_pid"] == my_pid:
                    lock_info = "本实例锁定"
                else:
                    lock_info = f"PID {s['lock_pid']} 锁定"
            remark = " · ".join(x for x in (marker, lock_info) if x)
            title_cell = _md_escape_cell(f"#{s['number']} {s['title']}")
            lines.append(
                f"| {s['number']} | {title_cell} | {s['turn_count']} | {_md_escape_cell(remark)} |"
            )
        print("\n".join(lines))
        print()
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
        print(f"  - {display}{marker} · {s['turn_count']} 轮{lock_info}")
    print()


def cmd_instance_handler(
    parts: list[str], sub_cmd: str, state: dict, *, markdown: bool = False
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
        list_instances,
        stop_instance_by_id,
        format_instances_markdown,
        format_instances_table,
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


def cmd_queue_status(message_queue: Any, *, markdown: bool = False) -> None:
    """查看消息队列状态。

    显示当前队列模式（queue / preemptive）以及
    每个聊天室的处理状态和等待消息数。

    Args:
        message_queue: 消息队列管理器实例
        markdown: True 时输出 GFM 表格（飞书 ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS``）
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


def format_schedule_command_usage() -> str:
    return (
        "定时任务（持久化在 MINI_AGENT_STATE/scheduled_tasks/，经消息队列跑 Agent）：\n"
        "  .schedule list\n"
        "  .schedule show <id>\n"
        "  .schedule remove <id>\n"
        "  .schedule enable <id>  |  .schedule disable <id>\n"
        "  .schedule add <id> every <秒> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>\n"
        "  .schedule add <id> once <ISO8601> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>\n"
        "  说明: 用 `` -- `` 分隔参数区与 prompt；once 的 naive 时间按 ``--tz``（默认 UTC）解释。\n"
        "  关闭调度: 环境变量 MINIAGENT_DISABLE_SCHEDULED_TASKS=1"
    )


def _schedule_head_strip_tz_tokens(tokens: list[str]) -> tuple[list[str], str]:
    """从 ``add ...`` 参数列表中去掉 ``--tz X``，返回 (新列表, 时区名)。"""
    tz = "UTC"
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "--tz" and i + 1 < len(tokens):
            tz = tokens[i + 1].strip() or "UTC"
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out, tz


def _parse_schedule_session_spec(token: str) -> Any:
    from miniagent.scheduled_tasks.models import SessionSpec

    t = token.strip()
    if t == "primary":
        return SessionSpec(mode="primary")
    if t == "ephemeral":
        return SessionSpec(mode="ephemeral")
    if t.startswith("fixed:"):
        sid = t[6:].strip()
        if not sid:
            raise ValueError("fixed: 后须填写会话 ID（如 default 或 feishu:oc_xxx）")
        feishu_chat: str | None = None
        if sid.startswith("feishu:"):
            feishu_chat = sid[7:].strip() or None
        return SessionSpec(mode="fixed", session_id=sid, feishu_chat_id=feishu_chat)
    raise ValueError(f"未知会话说明 {token!r}，须为 primary / ephemeral / fixed:...")


def cmd_schedule(text: str, *, allow_mutations: bool) -> str:
    from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec
    from miniagent.scheduled_tasks.store import compute_initial_next_run, load_tasks, save_tasks

    raw = (text or "").strip()
    if not raw.lower().startswith(".schedule"):
        return format_schedule_command_usage()
    rest = raw[9:].strip()  # len(".schedule")
    if not rest:
        return format_schedule_command_usage()
    parts = rest.split()
    sub = parts[0].lower()

    if sub == "list":
        tasks = load_tasks()
        if not tasks:
            return "（暂无定时任务）"
        lines = ["定时任务:"]
        for t in tasks:
            nxt = t.next_run_at
            nxt_s = f"{nxt:.0f}" if nxt is not None else "-"
            lines.append(
                f"  • {t.id}  ({t.name})  enabled={t.enabled}  "
                f"{t.schedule.kind}  next={nxt_s}  runs={t.run_count}"
            )
            if t.last_error:
                err = t.last_error.replace("\n", " ")[:160]
                lines.append(f"      err: {err}")
        return "\n".join(lines)

    if sub == "show" and len(parts) >= 2:
        tid = parts[1]
        for t in load_tasks():
            if t.id == tid:
                return json.dumps(t.to_json(), ensure_ascii=False, indent=2)
        return f"未找到任务: {tid}"

    if not allow_mutations:
        if sub in ("add", "remove", "enable", "disable"):
            return "⚠️ 当前渠道不允许修改定时任务，请在本地 MiniAgent CLI 执行。"

    if sub == "remove" and len(parts) >= 2:
        tid = parts[1]
        tasks = load_tasks()
        new = [x for x in tasks if x.id != tid]
        if len(new) == len(tasks):
            return f"未找到任务: {tid}"
        save_tasks(new)
        return f"✅ 已删除任务 {tid}"

    if sub == "enable" and len(parts) >= 2:
        tid = parts[1]
        tasks = load_tasks()
        for t in tasks:
            if t.id == tid:
                t.enabled = True
                if t.next_run_at is None:
                    t.next_run_at = compute_initial_next_run(t)
                save_tasks(tasks)
                return f"✅ 已启用 {tid}"
        return f"未找到任务: {tid}"

    if sub == "disable" and len(parts) >= 2:
        tid = parts[1]
        tasks = load_tasks()
        for t in tasks:
            if t.id == tid:
                t.enabled = False
                save_tasks(tasks)
                return f"✅ 已禁用 {tid}"
        return f"未找到任务: {tid}"

    if sub == "add":
        marker = " -- "
        if marker not in raw:
            return "缺少 `` -- `` 分隔符（用于分隔会话参数与 prompt）。\n" + format_schedule_command_usage()
        head, prompt = raw.split(marker, 1)
        prompt = prompt.strip()
        if not prompt:
            return "prompt 不能为空"
        head0 = head.strip()
        if head0.lower().startswith(".schedule"):
            head0 = head0[9:].strip()
        hparts = head0.split()
        hparts, tz_name = _schedule_head_strip_tz_tokens(hparts)
        if len(hparts) < 5 or hparts[0].lower() != "add":
            return "参数不足。\n" + format_schedule_command_usage()
        tid = hparts[1]
        kind = hparts[2].lower()
        try:
            if kind == "every":
                sec = int(hparts[3], 10)
                if sec <= 0:
                    return "间隔须为正整数"
                sess = _parse_schedule_session_spec(hparts[4])
                task = ScheduledTask(
                    id=tid,
                    name=tid,
                    prompt=prompt,
                    enabled=True,
                    schedule=ScheduleSpec(
                        kind="interval",
                        interval_seconds=sec,
                        timezone=tz_name,
                    ),
                    session=sess,
                )
                task.next_run_at = compute_initial_next_run(task)
            elif kind == "once":
                iso = hparts[3]
                sess = _parse_schedule_session_spec(hparts[4])
                task = ScheduledTask(
                    id=tid,
                    name=tid,
                    prompt=prompt,
                    enabled=True,
                    schedule=ScheduleSpec(
                        kind="once",
                        once_at_iso=iso,
                        timezone=tz_name,
                    ),
                    session=sess,
                )
                task.next_run_at = compute_initial_next_run(task)
                if task.next_run_at is None:
                    return "无法解析 once 时间，请使用 ISO8601（可含 Z 或 +08:00）"
                if task.next_run_at < time.time():
                    return "一次性任务时间已在过去，请使用未来时间"
            else:
                return "调度类型须为 every 或 once。\n" + format_schedule_command_usage()
        except ValueError as e:
            return f"❌ {e}"

        tasks = load_tasks()
        if any(x.id == tid for x in tasks):
            return f"任务 ID 已存在: {tid}"
        tasks.append(task)
        save_tasks(tasks)
        return f"✅ 已添加任务 {tid}，next_run_at={task.next_run_at}"

    return format_schedule_command_usage()


def format_help_markdown(
    model_profiles: dict[str, Any],
    active_profile: str,
    message_queue: Any,
    instance_id: int | None = None,
) -> str:
    """生成 `.help` 的 Markdown 正文（表格分组），供 CLI 打印与飞书 capture 复用。"""
    profiles = ", ".join(model_profiles.keys())
    mode = message_queue.mode.value
    inst_line = f"\n当前实例：**#{instance_id}**" if instance_id else ""

    header = "\n".join(
        [
            "## Mini Agent 命令",
            "",
            f"消息队列模式：**{mode}** ｜ 当前模型预设：**{active_profile}** ｜ 可用预设：`{profiles}`{inst_line}",
            "",
        ]
    )

    sections: list[str] = [
        _md_help_section(
            "启动命令（在操作系统终端执行）",
            None,
            [
                ("`python -m miniagent`", "启动 CLI 模式"),
                ("`python -m miniagent --feishu`", "启动 CLI + 飞书"),
                ("`python -m miniagent --stop`", "列出实例；交互选择停止"),
                ("`python -m miniagent --stop --all`", "停止全部实例"),
                ("`python -m miniagent --stop <id>...`", "停止指定实例 ID"),
            ],
        ),
        _md_help_section(
            "实例管理",
            None,
            [
                ("`.instance list`", "列出所有运行实例"),
                ("`.instance stop <id>`", "停止指定实例"),
            ],
        ),
        _md_help_section(
            "会话管理",
            "编号与原始 ID 均可，例如 `.session switch 1` 或 `.session switch default`。",
            [
                ("`.session list`", "列出所有会话"),
                ("`.session switch <编号/ID>`", "切换到指定会话"),
                ("`.session create <ID> [标题]`", "创建新会话，可指定标题"),
                ("`.session rename <编号/ID> <新标题>`", "重命名会话"),
            ],
        ),
        _md_help_section(
            "飞书控制",
            None,
            [
                ("`.feishu start`", "启动飞书 WebSocket 连接"),
                ("`.feishu stop`", "停止飞书连接"),
                ("`.feishu status`", "查看飞书运行状态"),
            ],
        ),
        _md_help_section(
            "通道绑定",
            "绑定后 CLI 与飞书共享同一会话，记忆、文件与工具互通。",
            [
                ("`.bind status`", "查看通道绑定状态"),
                ("`.bind cli <会话>`", "CLI 绑定到指定会话"),
                ("`.bind feishu <sender> <会话>`", "飞书私聊绑定到指定会话"),
                ("`.unbind cli`", "解除 CLI 绑定"),
                ("`.unbind feishu <sender>`", "解除飞书私聊绑定"),
                ("`.unbind all`", "解除所有绑定"),
            ],
        ),
        _md_help_section(
            "消息队列",
            "`queue` 为默认；`preemptive` 允许新消息插队。`.queue abort` / `.abort` 取消本 `chat_id` 上经 `dispatch` / `dispatch_wait` 投递的任务，**不是** `.stop`（停实例）。飞书侧可随时发送以打断卡住的 Agent；全屏 CLI 在单轮 Agent 执行中无法再次输入点命令。",
            [
                ("`.queue status`", "查看队列状态"),
                ("`.queue set <模式>`", "切换 `queue` / `preemptive`"),
                ("`.queue abort`", "中止本通道队列内运行中与排队的任务；不退出进程"),
                ("`.abort`", "同上（短命令）"),
            ],
        ),
        _md_help_section(
            "定时任务",
            "用 `` -- `` 分隔参数与 prompt；once 可加 ``--tz``；飞书侧仅 list/show。",
            [
                ("`.schedule list`", "列出任务"),
                ("`.schedule show <id>`", "查看 JSON"),
                ("`.schedule add ...`", "interval/once（见无参 `.schedule`）；Agent 可用 manage_scheduled_task"),
                ("`.schedule remove|enable|disable <id>`", "管理任务"),
            ],
        ),
        _md_help_section(
            "模型预设",
            None,
            [
                ("`.profile <名称>`", "切换模型预设"),
            ],
        ),
        _md_help_section(
            "工具与统计",
            None,
            [
                ("`.stats`", "查看工具调用统计"),
                ("`.status`", "查看系统运行状态"),
            ],
        ),
        _md_help_section(
            "实例控制",
            None,
            [
                (
                    "`.stop`",
                    (
                        f"停止当前实例并退出（实例 #{instance_id}）"
                        if instance_id
                        else "停止当前实例并退出"
                    ),
                ),
            ],
        ),
        _md_help_section(
            "其他",
            None,
            [
                ("`.help`", "显示本帮助"),
                ("`.copy`", "复制当前会话 transcript 到剪贴板（全屏 CLI）"),
                ("`quit` / `exit`", "退出程序"),
            ],
        ),
    ]

    footer = "\n".join(
        [
            "> 提示：直接输入文字即可与 Agent 对话。",
            "",
        ]
    )

    return header + "".join(sections) + footer


def cmd_help(
    model_profiles: dict[str, Any],
    active_profile: str,
    message_queue: Any,
    instance_id: int | None = None,
) -> None:
    """显示分类帮助信息。

    按功能分组展示所有可用命令（Markdown 表格，便于飞书 lark_md 渲染）。

    Args:
        model_profiles: 可用的模型预设配置
        active_profile: 当前使用的模型预设
        message_queue: 消息队列管理器实例
        instance_id: 当前实例 ID（可选）
    """
    print(format_help_markdown(model_profiles, active_profile, message_queue, instance_id))


__all__ = [
    "cmd_schedule",
    "format_schedule_command_usage",
    "sync_channel_router_to_session",
    "cmd_session_list",
    "cmd_session_switch",
    "cmd_session_create",
    "cmd_session_rename",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
    "format_help_markdown",
    "feishu_markdown_commands_enabled",
    "format_session_command_usage",
    "format_queue_command_usage",
]
