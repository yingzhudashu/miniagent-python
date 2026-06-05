"""CLI 命令处理模块

本模块包含所有 CLI 交互命令的实现，从 unified.py 拆分而来。

功能包括：
- 会话管理：列出、切换、创建、重命名、删除会话
- 实例管理：列出运行中的实例、停止指定实例
- 消息队列：查看队列状态、切换队列模式
- 通道绑定：/bind / /unbind 子命令
- 定时任务：/schedule add/list/remove/enable/disable
- 帮助显示：分类展示所有可用命令

注意：所有会话命令同时支持**编号**（如 1）和**原始 ID**（如 default）。

终端帮助正文与表格格式维护请与 ``docs/CLI.md`` 对齐。

**模块拆分**：以下命令已拆分到 ``miniagent/engine/commands/`` 子包：
- kb_commands: 知识库命令（cmd_kb_*）
- instance_commands: 实例管理（cmd_instance_handler）
- config_commands: 配置检查（feishu_*_enabled）
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

_logger = logging.getLogger(__name__)

# 性能优化：预编译高频正则表达式
_QUALITY_EVAL_SUGGESTIONS_PATTERN = re.compile(
    r"---\n🤖 .*?质量评分.*?\n\n建议：\n((?:- .+\n?)+)"
)

# 从拆分模块导入（向后兼容）
from miniagent.engine.commands.config_commands import (
    feishu_dot_commands_full_enabled,
    feishu_markdown_commands_enabled,
    format_test_command_usage,
)
from miniagent.engine.commands.instance_commands import cmd_instance_handler
from miniagent.engine.commands.kb_commands import (
    cmd_kb_list,
    cmd_kb_mount,
    cmd_kb_reload,
    cmd_kb_search,
    cmd_kb_unmount,
    format_kb_command_usage,
)

from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def format_session_command_usage() -> str:
    """与 ``format_help_markdown`` 中会话小节一致的用法说明（CLI 提示与 dispatch 共用）。"""
    return (
        "用法:\n"
        "  /session list                   列出所有会话\n"
        "  /session switch <编号/ID>       切换到指定会话（飞书默认仅 list；MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时与 CLI 同等）\n"
        "  /session create <ID> [标题]     创建新会话\n"
        "  /session rename <编号/ID> <标题>  重命名会话\n"
        "  /session delete <编号/ID>       删除指定会话（不能删除当前活跃会话）"
    )


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


def _md_escape_cell(text: str) -> str:
    """表格单元格：去掉换行并转义管道符。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|").replace("\n", " ").strip()
    return s


def _md_help_section(title: str, hint: str | None, rows: list[tuple[str, str]]) -> str:
    """生成分组 Markdown：可选引用提示 + 粗体命令列表（飞书 lark_md 友好）。

    避免使用 GFM 表格（飞书不支持），改用粗体 + 列表格式，
    使 CLI Markdown 渲染和飞书 lark_md 都能正常显示。
    """
    lines: list[str] = [f"### {title}", ""]
    if hint:
        lines.append(f"> {hint}")
        lines.append("")
    # 使用列表格式，命令用粗体，说明紧跟其后（飞书和 CLI 都友好）
    for cmd, desc in rows:
        # 粗体命令 + 分隔符 + 说明
        lines.append(f"- **{cmd}** — {desc}")
    lines.append("")
    return "\n".join(lines)


def sync_channel_router_to_session(
    channel_router: Any,
    session_id: str,
    feishu_p2p_synced_senders: set[str] | None,
) -> None:
    """将 CLI 与「自动同步」的飞书私聊通道绑定到同一主会话，并更新 primary。"""
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.cli_feishu_policy import (
        normalize_bind_session_id,
        should_sync_p2p_on_session_switch,
    )

    if channel_router is None:
        return
    sid = normalize_bind_session_id("cli", session_id)
    channel_router.bind(ChannelRouter.CLI_CHANNEL, sid)
    channel_router.set_primary(sid)
    if feishu_p2p_synced_senders and should_sync_p2p_on_session_switch(channel_router, sid):
        pfx = ChannelRouter.FEISHU_P2P_PREFIX
        for sender in feishu_p2p_synced_senders:
            channel_router.bind(f"{pfx}{sender}", sid)


def _save_cli_session_state_on_switch(
    session_manager: Any,
    session_id: str,
    channel_router: Any | None,
) -> None:
    """保存 CLI 上次会话状态到持久化（切换会话时调用）。"""
    if not channel_router:
        return
    try:
        sessions = session_manager.list_all_sessions_with_info()
        for s in sessions:
            if s.get("session_id") == session_id or s.get("id") == session_id:
                session_number = s.get("session_number", 0)
                session_title = s.get("title", "")
                channel_router.save_cli_session_state(
                    session_id,
                    session_number,
                    session_title,
                )
                return
    except Exception as e:
        _logger.debug("同步会话状态到通道失败: %s", e)


def _get_last_qa_with_metadata(
    session_manager: Any,
    session_id: str,
) -> tuple[dict | None, dict | None]:
    """获取当前会话的最后一轮 Q&A（带 metadata）。

    用于 .improve 和 .review 命令获取上一轮对话上下文。

    Args:
        session_manager: 会话管理器实例
        session_id: 当前会话 ID

    Returns:
        (user_msg_dict, assistant_msg_dict)
        消息字典包含 role、content、metadata 等字段。
    """
    import json
    import os

    session = session_manager.get(session_id)
    if session is None:
        return None, None

    # 优先从内存中的 conversation_history 读取
    history = getattr(session, "conversation_history", None) or []
    if not history:
        # 回退到 history.json
        files_path = getattr(session, "workspace_path", None) or getattr(session, "files_path", None)
        if files_path:
            hp = os.path.join(os.path.dirname(files_path), "history.json")
            if os.path.isfile(hp):
                try:
                    with open(hp, encoding="utf-8-sig") as f:
                        history = json.load(f)
                except Exception:
                    history = []

    last_user = None
    last_assistant = None

    for msg in reversed(history):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            if role == "user" and last_user is None:
                last_user = msg
            elif role == "assistant" and last_assistant is None:
                last_assistant = msg
            if last_user and last_assistant:
                break

    return last_user, last_assistant


def _extract_improve_suggestions(assistant_msg: dict) -> list[str]:
    """从 assistant 消息中提取质量评估改进建议。

    解析消息末尾的质量评估尾部文本，提取建议列表。

    Args:
        assistant_msg: assistant 消息字典

    Returns:
        建议列表（每条建议为字符串），无建议时返回空列表
    """
    content = assistant_msg.get("content", "")
    if not content:
        return []

    # 性能优化：使用预编译正则（避免每次都编译）
    match = _QUALITY_EVAL_SUGGESTIONS_PATTERN.search(content)

    if not match:
        return []

    suggestions_block = match.group(1)
    suggestions = []

    for line in suggestions_block.strip().split("\n"):
        if line.startswith("- "):
            suggestions.append(line[2:].strip())

    return suggestions


def _has_quality_evaluation(assistant_msg: dict) -> bool:
    """检查 assistant 消息是否包含质量评估尾部。

    Args:
        assistant_msg: assistant 消息字典

    Returns:
        True 如果包含质量评估尾部
    """
    content = assistant_msg.get("content", "")
    return "---\n🤖 " in content and "质量评分" in content


def _resolve_session(session_manager: Any, id_or_number: str) -> str | None:
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
        print(f"{WARNING_PREFIX} 会话管理器未初始化")
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


# cmd_instance_handler 已移至 miniagent/engine/commands/instance_commands.py


async def cmd_session_switch(
    session_manager: Any,
    active_session_id: str,
    id_or_number: str,
    try_lock_session_async: Any,
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
        try_lock_session_async: 异步尝试获取会话锁的函数
        release_session_lock: 释放会话锁的函数
        is_session_locked: 检查会话是否被锁定的函数

    Returns:
        新的活跃会话 ID（切换失败则返回原 ID）
    """
    if not session_manager:
        print(f"{WARNING_PREFIX} 会话管理器未初始化")
        return active_session_id

    # 解析目标会话 ID
    session_id = _resolve_session(session_manager, id_or_number)
    if not session_id:
        print(f"{ERROR_PREFIX} 会话不存在: {id_or_number}")
        return active_session_id

    # 释放当前会话锁
    release_session_lock(active_session_id)

    # 检查目标会话是否被其他实例锁定
    lock_pid = is_session_locked(session_id)
    if lock_pid is not None:
        # 尝试恢复会话（如果尚未加载）
        try:
            session_manager.get_or_create(session_id)
        except Exception as e:
            _logger.debug("恢复会话失败: %s", e)

        # 确认是否真的被锁定
        locked_sessions = [
            s
            for s in session_manager.list_all_sessions_with_info()
            if s["id"] == session_id and s["locked"]
        ]
        if locked_sessions:
            print(
                f"{WARNING_PREFIX} 会话 #{locked_sessions[0]['number']} "
                f"{locked_sessions[0]['title']} 被其他实例占用 (PID={lock_pid})"
            )
            # 重新锁定当前会话
            await try_lock_session_async(active_session_id)
            return active_session_id

    # 确保目标会话已加载
    try:
        session_manager.get_or_create(session_id)
    except Exception as e:
        _logger.debug("同步会话状态到通道失败: %s", e)

    # 获取目标会话锁
    ok, reason = await try_lock_session_async(session_id)
    if not ok:
        print(f"{ERROR_PREFIX} 无法切换: {reason}")
        await try_lock_session_async(active_session_id)
        return active_session_id

    # 切换成功：CLI 与自动同步的飞书私聊跟到同一 session_key
    active_session_id = session_id
    sync_channel_router_to_session(channel_router, session_id, feishu_p2p_synced_senders)
    display = session_manager.get_session_display_name(session_id)
    print(f"🔄 已切换到会话: {display}")

    # 保存 CLI 上次会话状态（--continue 功能）
    _save_cli_session_state_on_switch(session_manager, session_id, channel_router)

    return active_session_id


async def cmd_session_create(
    session_manager: Any, session_id: str, title: str | None, try_lock_session_async: Any
) -> None:
    """创建新会话并自动获取锁。

    Args:
        session_manager: 会话管理器实例
        session_id: 新会话的唯一标识
        title: 会话标题（可选，默认为空）
        try_lock_session_async: 异步尝试获取会话锁的函数
    """
    if not session_manager:
        print(f"{WARNING_PREFIX} 会话管理器未初始化")
        return

    from miniagent.session.manager import SessionOptions

    # 创建会话配置
    session_opts = SessionOptions(
        title=title or "",
        description=title or session_id,
    )
    session_manager.get_or_create(session_id, session_opts)

    # 获取新会话的锁
    await try_lock_session_async(session_id)

    display = session_manager.get_session_display_name(session_id)
    print(f"{SUCCESS_PREFIX} 已创建会话: {display}")


def cmd_session_rename(session_manager: Any, id_or_number: str, new_title: str) -> None:
    """重命名指定会话。

    Args:
        session_manager: 会话管理器实例
        id_or_number: 会话编号（如 1）或原始 ID
        new_title: 新的会话标题
    """
    if not session_manager:
        print(f"{WARNING_PREFIX} 会话管理器未初始化")
        return

    session_id = _resolve_session(session_manager, id_or_number)
    if not session_id:
        print(f"{ERROR_PREFIX} 会话不存在: {id_or_number}")
        return

    ok = session_manager.rename_session(session_id, new_title)
    if ok:
        display = session_manager.get_session_display_name(session_id)
        print(f"{SUCCESS_PREFIX} 已重命名: {display}")
    else:
        print(f"{ERROR_PREFIX} 重命名失败")


def cmd_session_delete(
    session_manager: Any,
    active_session_id: str,
    id_or_number: str,
    release_session_lock: Any,
    *,
    keep_files: bool = True,
) -> None:
    """删除指定会话（不能删除当前活跃会话）。

    Args:
        session_manager: 会话管理器实例
        active_session_id: 当前活跃会话 ID
        id_or_number: 会话编号（如 1）或原始 ID
        release_session_lock: 释放会话锁的函数
        keep_files: 是否保留工作空间文件（默认 True）
    """
    if not session_manager:
        print(f"{WARNING_PREFIX} 会话管理器未初始化")
        return

    session_id = _resolve_session(session_manager, id_or_number)
    if not session_id:
        print(f"{ERROR_PREFIX} 会话不存在: {id_or_number}")
        return

    if session_id == active_session_id:
        print(f"{ERROR_PREFIX} 不能删除当前活跃会话，请先 /session switch 到其他会话")
        return

    display = session_manager.get_session_display_name(session_id)

    # 释放锁（如果当前进程持有）
    try:
        release_session_lock(session_id)
    except Exception as e:
        _logger.debug("同步会话状态到通道失败: %s", e)

    ok = session_manager.destroy(session_id, keep_files=keep_files)
    if ok:
        action = "已删除（保留文件）" if keep_files else "已删除（清除文件）"
        print(f"{SUCCESS_PREFIX} {display} {action}")
    else:
        print(f"{ERROR_PREFIX} 删除失败: {display}")


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
        print(f"{SUCCESS_PREFIX} 已切换到队列模式（消息按顺序处理）")
    elif mode_str == "preemptive":
        message_queue.mode = QueueMode.PREEMPTIVE
        print(f"{SUCCESS_PREFIX} 已切换到打断模式（最新消息打断前面处理）")
    else:
        print(f"{ERROR_PREFIX} 未知模式: {mode_str}")
        print("   可用: queue, preemptive")


def cmd_bind(channel_router: Any, args: list[str], state: dict[str, Any] | None = None) -> str:
    """绑定通道到指定会话。

    用法:
        /bind cli <会话>      将 CLI 通道绑定到指定会话
        /bind feishu <会话>   将飞书私聊绑定到指定会话（需 sender_id）
        /bind status          查看所有绑定状态

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
            "  /bind status              查看绑定状态\n"
            "  /bind cli <会话>          CLI 绑定到指定会话\n"
            "  /bind feishu <sender> <会话>  飞书私聊绑定（需 sender_id）"
        )

    channel = args[0].lower()

    if channel == "cli":
        from miniagent.infrastructure.cli_feishu_policy import normalize_bind_session_id

        session_id = normalize_bind_session_id("cli", args[1])
        old = channel_router.bind(ChannelRouter.CLI_CHANNEL, session_id)
        channel_router.set_primary(session_id)
        old_msg = f"（原绑定: {old}）" if old else ""
        return f"{SUCCESS_PREFIX} CLI 已绑定到会话: {session_id} {old_msg}"

    elif channel == "feishu":
        if len(args) < 3:
            return "飞书私聊绑定需要 sender_id: /bind feishu <sender_id> <会话>"
        from miniagent.infrastructure.cli_feishu_policy import (
            normalize_bind_session_id,
            p2p_bind_target_allowed,
        )

        sender_id = args[1]
        session_id = normalize_bind_session_id("feishu", args[2])
        ok, err = p2p_bind_target_allowed(channel_router, session_id)
        if not ok:
            return f"{ERROR_PREFIX} {err}"
        channel_id = f"{ChannelRouter.FEISHU_P2P_PREFIX}{sender_id}"
        old = channel_router.bind(channel_id, session_id)
        old_msg = f"（原绑定: {old}）" if old else ""
        if state is not None:
            synced = state.setdefault("feishu_p2p_synced_senders", set())
            if isinstance(synced, set):
                synced.discard(sender_id)
        return f"{SUCCESS_PREFIX} 飞书私聊 ({sender_id[:8]}...) 已绑定到: {session_id} {old_msg}"

    return f"{ERROR_PREFIX} 未知通道: {channel}"


def cmd_unbind(channel_router: Any, args: list[str], state: dict[str, Any] | None = None) -> str:
    """解除通道绑定。

    用法:
        /unbind cli       解除 CLI 绑定
        /unbind feishu <sender>  解除飞书私聊绑定
        /unbind all       解除所有绑定

    Args:
        channel_router: ChannelRouter 实例
        args: 命令参数

    Returns:
        结果消息
    """
    from miniagent.infrastructure.channel_router import ChannelRouter

    if not args or args[0] == "":
        return "用法: /unbind cli | /unbind feishu <sender> | /unbind all"

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
        return f"{SUCCESS_PREFIX} 已解除 {count} 个通道绑定"

    elif target == "cli":
        old = channel_router.unbind(ChannelRouter.CLI_CHANNEL)
        if old:
            return f"{SUCCESS_PREFIX} CLI 已解除绑定（原: {old}）"
        return "📭 CLI 未绑定任何会话"

    elif target == "feishu":
        if len(args) < 2:
            return "飞书私聊解绑需要 sender_id: /unbind feishu <sender_id>"
        sender_id = args[1]
        channel_id = f"{ChannelRouter.FEISHU_P2P_PREFIX}{sender_id}"
        old = channel_router.unbind(channel_id)
        if state is not None:
            synced = state.get("feishu_p2p_synced_senders")
            if isinstance(synced, set):
                synced.discard(sender_id)
        if old:
            return f"{SUCCESS_PREFIX} 飞书私聊 ({sender_id[:8]}...) 已解除绑定（原: {old}）"
        return "📭 该飞书私聊未绑定任何会话"

    return f"{ERROR_PREFIX} 未知通道: {target}"


def format_schedule_command_usage() -> str:
    """返回 ``/schedule`` 子命令的用法说明文本（终端与工具复用）。"""
    return (
        "定时任务（持久化在 MINIAGENT_PATHS_STATE_DIR/scheduled_tasks/，经消息队列跑 Agent）：\n"
        "  /schedule list\n"
        "  /schedule show <id>\n"
        "  /schedule remove <id>\n"
        "  /schedule enable <id>  |  /schedule disable <id>\n"
        "  /schedule update <id> every|once|cron ...（语法同 add，不含新建 id） [--tz IANA] -- <prompt>\n"
        "  /schedule add <id> every <秒> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>\n"
        "  /schedule add <id> once <ISO8601> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>\n"
        '  /schedule add <id> cron "<分> <时> <日> <月> <周>" <primary|...> [--tz IANA] -- <prompt>\n'
        "  说明: 用 `` -- `` 分隔参数区与 prompt；cron 为标准 5 段 Unix 表达式（半角 *）。\n"
        "  关闭调度: 环境变量 MINIAGENT_DISABLE_SCHEDULED_TASKS=1"
    )


def _schedule_head_strip_tz_tokens(tokens: list[str]) -> tuple[list[str], str | None, bool]:
    """从参数列表去掉 ``--tz X``，返回 (新列表, 时区或 None, 是否显式指定)。"""
    tz_override: str | None = None
    tz_explicit = False
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "--tz" and i + 1 < len(tokens):
            tz_override = tokens[i + 1].strip() or "UTC"
            tz_explicit = True
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out, tz_override, tz_explicit


def _resolve_schedule_tz(
    tz_override: str | None,
    tz_explicit: bool,
    *,
    existing: Any | None = None,
) -> tuple[str, bool]:
    """``add`` 用 env 默认；``update`` 未写 ``--tz`` 时保留原任务时区。"""
    from miniagent.scheduled_tasks.timezone_util import default_schedule_timezone

    if tz_override is not None:
        return tz_override, tz_explicit
    if existing is not None:
        return (
            (existing.schedule.timezone or "").strip() or default_schedule_timezone(),
            bool(existing.schedule.timezone_explicit),
        )
    return default_schedule_timezone(), False


def _parse_cron_add_tokens(tokens: list[str]) -> tuple[str, str]:
    """从 ``add <id> cron … <session>`` 的 token 列表解析 cron 与会话 token。"""
    if len(tokens) < 4 or tokens[0].lower() != "add" or tokens[2].lower() != "cron":
        raise ValueError("cron 参数不足")
    rest = tokens[3:]
    if len(rest) < 2:
        raise ValueError("cron 须为 5 段（分 时 日 月 周）及会话说明")
    sess_token = rest[-1]
    cron_parts = rest[:-1]
    if len(cron_parts) == 1:
        expr = cron_parts[0]
    elif len(cron_parts) == 5:
        expr = " ".join(cron_parts)
    else:
        raise ValueError("cron 须为 5 段（分 时 日 月 周），或使用引号包裹整段表达式")
    return expr, sess_token


def _parse_schedule_session_spec(token: str) -> Any:
    """解析 ``add`` 子命令中的会话目标 token，返回 :class:`~miniagent.scheduled_tasks.models.SessionSpec`。"""
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
    """处理 ``/schedule`` 命令：列出/展示/增删改定时任务；飞书等非变异渠道受 ``allow_mutations`` 限制。"""
    from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec
    from miniagent.scheduled_tasks.store import (
        compute_initial_next_run,
        format_next_run_display,
        load_tasks,
        repair_invalid_schedules,
        save_tasks,
    )

    raw = (text or "").strip()
    if not raw.lower().startswith("/schedule"):
        return format_schedule_command_usage()
    rest = raw[9:].strip()  # len("/schedule")
    if not rest:
        return format_schedule_command_usage()
    parts = rest.split()
    sub = parts[0].lower()

    if sub == "list":
        tasks = load_tasks()
        if not tasks:
            return "（暂无定时任务）"
        lines = ["定时任务:"]
        now = time.time()
        for t in tasks:
            nxt_s = format_next_run_display(t, now_ts=now)
            kind = t.schedule.kind
            if kind == "cron" and t.schedule.cron_expr:
                kind = f'cron "{t.schedule.cron_expr}"'
            lines.append(
                f"  • {t.id}  ({t.name})  enabled={t.enabled}  "
                f"{kind}  next={nxt_s}  runs={t.run_count}"
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
        if sub in ("add", "update", "remove", "enable", "disable"):
            return f"{WARNING_PREFIX} 当前渠道不允许修改定时任务，请在本地 MiniAgent CLI 执行。"

    if sub == "remove" and len(parts) >= 2:
        tid = parts[1]
        tasks = load_tasks()
        new = [x for x in tasks if x.id != tid]
        if len(new) == len(tasks):
            return f"未找到任务: {tid}"
        save_tasks(new)
        return f"{SUCCESS_PREFIX} 已删除任务 {tid}"

    if sub == "enable" and len(parts) >= 2:
        tid = parts[1]
        tasks = load_tasks()
        for t in tasks:
            if t.id == tid:
                t.enabled = True
                if t.next_run_at is None:
                    t.next_run_at = compute_initial_next_run(t)
                repair_invalid_schedules(tasks)
                save_tasks(tasks)
                return f"{SUCCESS_PREFIX} 已启用 {tid}"
        return f"未找到任务: {tid}"

    if sub == "disable" and len(parts) >= 2:
        tid = parts[1]
        tasks = load_tasks()
        for t in tasks:
            if t.id == tid:
                t.enabled = False
                save_tasks(tasks)
                return f"{SUCCESS_PREFIX} 已禁用 {tid}"
        return f"未找到任务: {tid}"

    if sub == "add":
        import shlex

        marker = " -- "
        if marker not in raw:
            return (
                "缺少 `` -- `` 分隔符（用于分隔会话参数与 prompt）。\n"
                + format_schedule_command_usage()
            )
        head, prompt = raw.split(marker, 1)
        prompt = prompt.strip()
        if not prompt:
            return "prompt 不能为空"
        head0 = head.strip()
        if head0.lower().startswith("/schedule"):
            head0 = head0[9:].strip()
        try:
            hparts = shlex.split(head0)
        except ValueError as e:
            return f"{ERROR_PREFIX} 参数解析失败: {e}"
        hparts, tz_override, tz_explicit_flag = _schedule_head_strip_tz_tokens(hparts)
        tz_name, tz_explicit = _resolve_schedule_tz(tz_override, tz_explicit_flag)
        if len(hparts) < 4 or hparts[0].lower() != "add":
            return "参数不足。\n" + format_schedule_command_usage()
        tid = hparts[1]
        kind = hparts[2].lower()
        try:
            if kind == "every":
                if len(hparts) < 5:
                    return "参数不足。\n" + format_schedule_command_usage()
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
                        timezone_explicit=tz_explicit,
                    ),
                    session=sess,
                )
                task.next_run_at = compute_initial_next_run(task)
            elif kind == "once":
                if len(hparts) < 5:
                    return "参数不足。\n" + format_schedule_command_usage()
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
                        timezone_explicit=tz_explicit,
                    ),
                    session=sess,
                )
                task.next_run_at = compute_initial_next_run(task)
                if task.next_run_at is None:
                    return "无法解析 once 时间，请使用 ISO8601（可含 Z 或 +08:00）"
                if task.next_run_at < time.time():
                    return "一次性任务时间已在过去，请使用未来时间"
            elif kind == "cron":
                from miniagent.scheduled_tasks.cron import validate_cron_expr

                cron_expr, sess_token = _parse_cron_add_tokens(hparts)
                cron_expr = validate_cron_expr(cron_expr)
                sess = _parse_schedule_session_spec(sess_token)
                task = ScheduledTask(
                    id=tid,
                    name=tid,
                    prompt=prompt,
                    enabled=True,
                    schedule=ScheduleSpec(
                        kind="cron",
                        cron_expr=cron_expr,
                        timezone=tz_name,
                        timezone_explicit=tz_explicit,
                    ),
                    session=sess,
                )
                task.next_run_at = compute_initial_next_run(task)
                if task.next_run_at is None:
                    return "无法根据 cron 计算下次触发时间"
            else:
                return "调度类型须为 every、once 或 cron。\n" + format_schedule_command_usage()
        except ValueError as e:
            return f"{ERROR_PREFIX} {e}"

        tasks = load_tasks()
        if any(x.id == tid for x in tasks):
            return f"任务 ID 已存在: {tid}"
        tasks.append(task)
        save_tasks(tasks)
        return (
            f"{SUCCESS_PREFIX} 已添加任务 {tid}，timezone={task.schedule.timezone}"
            f"，next={format_next_run_display(task)}"
        )

    if sub == "update":
        import shlex

        marker = " -- "
        if marker not in raw:
            return "缺少 `` -- `` 分隔符。\n" + format_schedule_command_usage()
        head, prompt = raw.split(marker, 1)
        prompt = prompt.strip()
        if not prompt:
            return "prompt 不能为空"
        head0 = head.strip()
        if head0.lower().startswith("/schedule"):
            head0 = head0[9:].strip()
        try:
            hparts = shlex.split(head0)
        except ValueError as e:
            return f"{ERROR_PREFIX} 参数解析失败: {e}"
        hparts, tz_override, tz_explicit_flag = _schedule_head_strip_tz_tokens(hparts)
        if len(hparts) < 4 or hparts[0].lower() != "update":
            return "参数不足。\n" + format_schedule_command_usage()
        tid = hparts[1]
        kind = hparts[2].lower()
        tasks = load_tasks()
        existing = next((x for x in tasks if x.id == tid), None)
        if existing is None:
            return f"未找到任务: {tid}"
        tz_name, tz_explicit = _resolve_schedule_tz(
            tz_override, tz_explicit_flag, existing=existing
        )
        try:
            if kind == "every":
                if len(hparts) < 5:
                    return "参数不足。\n" + format_schedule_command_usage()
                sec = int(hparts[3], 10)
                if sec <= 0:
                    return "间隔须为正整数"
                sess = _parse_schedule_session_spec(hparts[4])
                existing.prompt = prompt
                existing.schedule = ScheduleSpec(
                    kind="interval",
                    interval_seconds=sec,
                    timezone=tz_name,
                    timezone_explicit=tz_explicit,
                )
                existing.session = sess
            elif kind == "once":
                if len(hparts) < 5:
                    return "参数不足。\n" + format_schedule_command_usage()
                iso = hparts[3]
                sess = _parse_schedule_session_spec(hparts[4])
                existing.prompt = prompt
                existing.schedule = ScheduleSpec(
                    kind="once",
                    once_at_iso=iso,
                    timezone=tz_name,
                    timezone_explicit=tz_explicit,
                )
                existing.session = sess
            elif kind == "cron":
                from miniagent.scheduled_tasks.cron import validate_cron_expr

                cron_expr, sess_token = _parse_cron_add_tokens(["add", tid, "cron", *hparts[3:]])
                cron_expr = validate_cron_expr(cron_expr)
                sess = _parse_schedule_session_spec(sess_token)
                existing.prompt = prompt
                existing.schedule = ScheduleSpec(
                    kind="cron",
                    cron_expr=cron_expr,
                    timezone=tz_name,
                    timezone_explicit=tz_explicit,
                )
                existing.session = sess
            else:
                return "调度类型须为 every、once 或 cron。\n" + format_schedule_command_usage()
        except ValueError as e:
            return f"{ERROR_PREFIX} {e}"
        existing.enabled = True
        existing.last_error = None
        existing.next_run_at = compute_initial_next_run(existing)
        if existing.next_run_at is None:
            repair_invalid_schedules(tasks)
            save_tasks(tasks)
            return "无法计算下次触发时间（请检查调度参数）"
        repair_invalid_schedules(tasks)
        save_tasks(tasks)
        return (
            f"{SUCCESS_PREFIX} 已更新任务 {tid}，timezone={existing.schedule.timezone}"
            f"，next={format_next_run_display(existing)}"
        )

    return format_schedule_command_usage()


def format_help_markdown(
    message_queue: Any,
    instance_id: int | None = None,
) -> str:
    """生成 `/help` 的 Markdown 正文（表格分组），供 CLI 打印与飞书 capture 复用。"""
    mode = message_queue.mode.value
    inst_line = f"\n当前实例：**#{instance_id}**" if instance_id else ""

    header = "\n".join(
        [
            "## Mini Agent 命令",
            "",
            f"消息队列模式：**{mode}**{inst_line}",
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
                ("`/instance list`", "列出所有运行实例"),
                ("`/instance stop <id>`", "停止指定实例"),
            ],
        ),
        _md_help_section(
            "会话管理",
            "编号与原始 ID 均可，例如 `/session switch 1` 或 `/session switch default`。",
            [
                ("`/session list`", "列出所有会话"),
                ("`/session switch <编号/ID>`", "切换到指定会话"),
                ("`/session create <ID> [标题]`", "创建新会话，可指定标题"),
                ("`/session rename <编号/ID> <新标题>`", "重命名会话"),
                ("`/session delete <编号/ID>`", "删除会话（不可删除当前活跃会话）"),
            ],
        ),
        _md_help_section(
            "飞书控制",
            None,
            [
                ("`/feishu start`", "启动飞书 WebSocket 连接"),
                ("`/feishu stop`", "停止飞书连接"),
                ("`/feishu status`", "查看飞书运行状态"),
            ],
        ),
        _md_help_section(
            "通道绑定",
            "绑定后 CLI 与飞书共享同一会话，记忆、文件与工具互通。",
            [
                ("`/bind status`", "查看通道绑定状态"),
                ("`/bind cli <会话>`", "CLI 绑定到指定会话"),
                ("`/bind feishu <sender> <会话>`", "飞书私聊绑定到指定会话"),
                ("`/unbind cli`", "解除 CLI 绑定"),
                ("`/unbind feishu <sender>`", "解除飞书私聊绑定"),
                ("`/unbind all`", "解除所有绑定"),
            ],
        ),
        _md_help_section(
            "消息队列",
            "`queue` 为默认；`preemptive` 允许新消息插队。`/queue abort` / `/abort` 取消本 `chat_id` 上经 `dispatch` / `dispatch_wait` 投递的任务，**不是** `/stop`（停实例）。飞书侧可随时发送以打断卡住的 Agent；全屏 CLI 在单轮 Agent 执行中无法再次输入命令。",
            [
                ("`/queue status`", "查看队列状态"),
                ("`/query`", "同上（短命令）"),
                ("`/queue set <模式>`", "切换 `queue` / `preemptive`"),
                ("`/queue abort`", "中止本通道队列内运行中与排队的任务；不退出进程"),
                ("`/abort`", "同上（短命令）"),
            ],
        ),
        _md_help_section(
            "确认控制",
            "规划器判定高风险操作时会暂停等待确认。以下命令不经过消息队列，直接响应暂停点。",
            [
                ("`/confirm`", "批准当前待确认的规划，继续执行"),
                ("`/adjust <内容>`", "调整内容并批准"),
                ("`/reject`", "拒绝当前规划，取消操作"),
            ],
        ),
        _md_help_section(
            "答案改进",
            "根据质量评估建议改进上一轮答案；支持多轮改进。",
            [
                ("`/improve`", "根据质量评估建议改进上一轮答案"),
                ("`/improve --force`", "强制改进（即使质量已通过）"),
                ("`/improve --reset`", "回退到原始答案重新改进"),
                ("`/review`", "自我反驳式审查答案（迭代最多3轮）"),
            ],
        ),
        _md_help_section(
            "定时任务",
            "用 `` -- `` 分隔参数与 prompt；once 可加 ``--tz``；飞书默认仅 list/show，MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时与 CLI 同等。",
            [
                ("`/schedule list`", "列出任务"),
                ("`/schedule show <id>`", "查看 JSON"),
                (
                    "`/schedule add ...`",
                    "interval/once（见无参 `/schedule`）；Agent 可用 manage_scheduled_task",
                ),
                ("`/schedule update <id> …`", "修改任务（语法同 add）"),
                ("`/schedule remove|enable|disable <id>`", "管理任务"),
            ],
        ),
        _md_help_section(
            "自我优化",
            "基于运行日志和代码分析生成优化提案，默认仅生成不执行。配置 auto_apply:true 可自动执行低风险提案。",
            [
                ("`/self-opt status`", "查看自我优化系统状态"),
                ("`/self-opt proposals`", "列出待执行提案"),
                ("`/self-opt show <id>`", "查看提案详情"),
                ("`/self-opt approve <id>`", "批准提案"),
                ("`/self-opt reject <id>`", "拒绝提案"),
                ("`/self-opt apply <id>`", "执行已批准的提案"),
                ("`/self-opt analyze`", "触发运行分析"),
                ("`/self-opt report`", "查看分析报告"),
            ],
        ),
        _md_help_section(
            "知识库",
            "挂载本地文档供 Agent 检索；知识库目录应有 KB.yaml 或 files/ 子目录。",
            [
                ("`/kb list`", "列出已挂载的知识库"),
                ("`/kb mount <路径> [名称]`", "挂载知识库（目录或文件）"),
                ("`/kb unmount <名称>`", "卸载知识库"),
                ("`/kb search <关键词> [名称]`", "检索知识库内容"),
                ("`/kb reload [名称]`", "重新加载知识库"),
            ],
        ),
        _md_help_section(
            "工具与统计",
            None,
            [
                ("`/stats`", "查看工具调用统计"),
                ("`/status`", "查看系统运行状态"),
            ],
        ),
        _md_help_section(
            "自测命令",
            "测试样本位于 tests/evaluation/samples/；默认 mock 模式（不调用真实 LLM）。",
            [
                ("`/test run`", "运行所有测试"),
                ("`/test run <类别>`", "按类别过滤（security | prompt_injection | tool_selection | schema | regression | cost）"),
                ("`/test run <类别> <名称>`", "进一步按名称过滤（正则）"),
                ("`/test list`", "列出所有测试样本"),
                ("`/test status`", "查看最近测试结果"),
            ],
        ),
        _md_help_section(
            "实例控制",
            None,
            [
                (
                    "`/stop`",
                    (
                        f"停止当前实例并退出（实例 #{instance_id}）"
                        if instance_id
                        else "停止当前实例并退出"
                    ),
                ),
            ],
        ),
        _md_help_section(
            "后台任务",
            "并行执行子任务，不污染主对话历史。",
            [
                ("`/btw start <prompt>`", "启动后台任务"),
                ("`/btw status`", "查看任务列表"),
                ("`/btw result <id>`", "获取任务结果"),
                ("`/btw cancel <id>`", "取消任务"),
                ("`Ctrl+T`", "快捷键查看任务列表"),
            ],
        ),
        _md_help_section(
            "配置与诊断",
            None,
            [
                ("`/config`", "查看配置概览"),
                ("`/config <section>`", "查看特定配置部分"),
                ("`/model`", "显示当前模型"),
                ("`/model <model>`", "切换模型"),
                ("`/doctor`", "诊断安装与配置"),
            ],
        ),
        _md_help_section(
            "其他",
            None,
            [
                ("`/help`", "显示本帮助"),
                ("`/reload-skills`", "从磁盘重新加载技能（无需重启）"),
                ("`/copy [N]`", "复制最近第N条助手回复到剪贴板（全屏 CLI）"),
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


def cmd_improve(
    session_manager: Any,
    session_id: str,
    *,
    force: bool = False,
    reset: bool = False,
) -> tuple[dict, dict, list[str]] | tuple[str, bool]:
    """获取改进上一轮答案所需的上下文。

    从历史记录获取上一轮 Q&A，提取质量评估建议，
    供 command_dispatch.py 的 _run_improve() 使用。

    Args:
        session_manager: 会话管理器实例
        session_id: 当前会话 ID
        force: 强制改进（即使质量已通过）
        reset: 回退到原始答案重新改进

    Returns:
        成功: (user_msg_dict, assistant_msg_dict, suggestions)
        失败: (错误消息, False)
    """
    # 1. 获取最后一轮 Q&A
    last_user, last_assistant = _get_last_qa_with_metadata(session_manager, session_id)

    # 2. 检查边界情况
    if not last_assistant:
        return f"{WARNING_PREFIX} 当前会话无历史对话，无法改进", False

    # 3. 提取质量评估建议
    suggestions = _extract_improve_suggestions(last_assistant)

    if not suggestions:
        if _has_quality_evaluation(last_assistant):
            if force:
                # 强制改进模式：即使无建议也允许改进（返回空建议列表）
                return last_user, last_assistant, []
            return f"{SUCCESS_PREFIX} 上一轮质量评估已通过，无需改进（使用 `.improve --force` 强制改进）", False
        else:
            return f"{WARNING_PREFIX} 上一轮未启用质量评估，无法改进", False

    # 4. 检查是否已改进过（限制轮次）
    metadata = last_assistant.get("metadata", {})
    if metadata.get("improved") and not reset:
        improve_round = metadata.get("improve_round", 1)
        if improve_round >= 3:
            return f"{WARNING_PREFIX} 已达到改进轮次上限（3轮），建议重新提问或使用 `.review`", False

    # 5. 返回改进所需的上下文
    return last_user, last_assistant, suggestions


def cmd_copy_transcript(
    session_manager: Any,
    session_id: str,
    n: int = 1,
) -> str:
    """复制最近第N条助手回复到剪贴板。

    Args:
        session_manager: 会话管理器
        session_id: 当前会话ID
        n: 复制最近第N条回复（默认1，负数表示倒数）

    Returns:
        操作结果消息
    """
    from miniagent.engine.clipboard import copy_text_to_system_clipboard

    if session_manager is None:
        return f"{WARNING_PREFIX} 会话管理器未初始化"

    session = session_manager.get(session_id)
    if session is None:
        return f"{ERROR_PREFIX} 会话 {session_id} 不存在"

    # 获取历史文件路径
    files_path = getattr(session, "workspace_path", None) or getattr(session, "files_path", None)
    if not files_path:
        return f"{ERROR_PREFIX} 无法定位会话工作空间"

    history_path = os.path.join(os.path.dirname(files_path), "history.json")
    if not os.path.isfile(history_path):
        return f"{ERROR_PREFIX} 会话历史文件不存在"

    try:
        with open(history_path, encoding="utf-8-sig") as f:
            messages = json.load(f)

        # 提取所有助手消息
        assistant_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]

        if not assistant_msgs:
            return f"{ERROR_PREFIX} 没有助手回复可复制"

        # 计算索引（负数支持）
        if n < 0:
            idx = n  # 负数直接作为索引（-1 = 最后一条）
        else:
            idx = -n  # 正数转换为负索引（1 = -1 = 最后一条）

        try:
            msg = assistant_msgs[idx]
        except IndexError:
            return f"{ERROR_PREFIX} 索引 {n} 超出范围（共 {len(assistant_msgs)} 条助手回复）"

        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            return f"{ERROR_PREFIX} 该助手回复内容为空"

        # 复制到剪贴板
        success = copy_text_to_system_clipboard(content)
        if success:
            preview_len = min(100, len(content))
            preview = content[:preview_len].replace("\n", " ")
            if len(content) > preview_len:
                preview += "..."
            return f"{SUCCESS_PREFIX} 已复制第 {abs(idx)} 条助手回复（{len(content)} 字符）\n   预览: {preview}"
        else:
            return f"{ERROR_PREFIX} 复制到剪贴板失败"

    except Exception as e:
        return f"{ERROR_PREFIX} 读取历史失败: {e}"


# ─── 自我优化命令 ────────────────────────────────────────


def cmd_self_opt_status() -> None:
    """显示自我优化系统状态。

    输出：
    - 系统启用状态
    - auto_apply 配置
    - 提案存储路径
    - 今日提案数量
    """
    from miniagent.infrastructure.json_config import get_config
    from miniagent.core.self_opt.proposal_store import (
        ProposalStore,
        get_proposal_output_dir,
    )

    enabled = get_config("self_optimization.enabled", True)
    auto_apply = get_config("self_optimization.auto_apply", False)
    max_risk = get_config("self_optimization.auto_apply_max_risk", "low")
    output_dir = get_proposal_output_dir()
    runtime_enabled = get_config("self_optimization.runtime_analysis_enabled", True)
    code_enabled = get_config("self_optimization.code_analysis_enabled", True)

    # 统计今日提案
    store = ProposalStore()
    proposals = store.load_proposals()
    pending_count = len([p for p in proposals if p.get("status") == "pending"])

    print("\n🔧 自我优化系统状态:")
    print(f"  系统启用: {'✅ 是' if enabled else '❌ 否'}")
    print(f"  自动执行: {'✅ 是' if auto_apply else '❌ 否（仅生成提案）'}")
    print(f"  自动执行风险上限: {max_risk}")
    print(f"  运行日志分析: {'✅ 启用' if runtime_enabled else '❌ 禁用'}")
    print(f"  代码静态分析: {'✅ 启用' if code_enabled else '❌ 禁用'}")
    print(f"  提案存储路径: {output_dir}")
    print(f"  今日待执行提案: {pending_count} 个")
    print()


def cmd_self_opt_proposals(status: str | None = None) -> None:
    """列出提案。

    Args:
        status: 状态过滤（pending/approved/rejected/completed/executing/failed）
    """
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    proposals = store.load_proposals(status=status)

    if not proposals:
        status_label = status or "全部"
        print(f"\n📭 {status_label}提案: 暂无\n")
        return

    print(f"\n📋 提案列表 ({status or '全部'}):\n")

    status_icons = {
        "pending": "⏳",
        "approved": "✅",
        "rejected": "❌",
        "executing": "🔄",
        "completed": "🎉",
        "failed": "⚠️",
    }
    risk_colors = {"low": "", "medium": "", "high": ""}

    for p in proposals:
        icon = status_icons.get(p.get("status", "pending"), "❓")
        proposal_data = p.get("proposal", {})
        risk = proposal_data.get("risk_level", "low")
        source = p.get("source", "?")
        desc_preview = proposal_data.get("description", "")[:50]

        print(f"  {icon} {p.get('id', '?')}")
        print(f"     来源: {source}, 风险: {risk}")
        print(f"     描述: {desc_preview}...")
        print(f"     状态: {p.get('status', 'pending')}, 工时: {proposal_data.get('estimated_effort', 0)}min")
        print()

    print(f"总计: {len(proposals)} 个提案\n")


def cmd_self_opt_show(proposal_id: str) -> None:
    """显示提案详情。

    Args:
        proposal_id: 提案 ID
    """
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    record = store.get_proposal(proposal_id)

    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return

    proposal = record.get("proposal", {})

    print(f"\n📄 提案详情: {proposal_id}\n")
    print(f"  状态: {record.get('status', 'pending')}")
    print(f"  来源: {record.get('source', '?')}")
    print(f"  创建时间: {record.get('created_at', '?')}")
    print(f"  更新时间: {record.get('updated_at', '?')}")
    print()
    print(f"  类型: {proposal.get('type', '?')}")
    print(f"  风险等级: {proposal.get('risk_level', 'low')}")
    print(f"  目标: {proposal.get('target', '')}")
    print(f"  描述: {proposal.get('description', '')}")
    print()
    print(f"  理由: {proposal.get('rationale', '')}")
    print(f"  预期收益: {proposal.get('expected_benefit', '')}")
    print(f"  预估工时: {proposal.get('estimated_effort', 0)} 分钟")
    print()

    # 文件变更
    files = proposal.get("files", [])
    if files:
        print("  文件变更:")
        for f in files:
            print(f"    - {f.get('action', '?')}: {f.get('path', '')}")
            if f.get("reason"):
                print(f"      原因: {f.get('reason')}")
        print()

    # 测试用例
    test_cases = proposal.get("test_cases", [])
    if test_cases:
        print("  测试用例:")
        for tc in test_cases:
            print(f"    - {tc.get('id', '?')}: {tc.get('description', '')}")
        print()


def cmd_self_opt_approve(proposal_id: str) -> None:
    """批准提案。

    Args:
        proposal_id: 提案 ID
    """
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    record = store.get_proposal(proposal_id)

    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return

    current_status = record.get("status", "pending")
    if current_status != "pending":
        print(f"\n{WARNING_PREFIX} 提案当前状态为 {current_status}，无法批准\n")
        return

    success = store.update_status(proposal_id, "approved")
    if success:
        print(f"\n{SUCCESS_PREFIX} 提案 {proposal_id} 已批准\n")
    else:
        print(f"\n{ERROR_PREFIX} 批准失败\n")


def cmd_self_opt_reject(proposal_id: str) -> None:
    """拒绝提案。

    Args:
        proposal_id: 提案 ID
    """
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    record = store.get_proposal(proposal_id)

    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return

    current_status = record.get("status", "pending")
    if current_status != "pending":
        print(f"\n{WARNING_PREFIX} 提案当前状态为 {current_status}，无法拒绝\n")
        return

    success = store.update_status(proposal_id, "rejected")
    if success:
        print(f"\n{SUCCESS_PREFIX} 提案 {proposal_id} 已拒绝\n")
    else:
        print(f"\n{ERROR_PREFIX} 拒绝失败\n")


async def cmd_self_opt_apply(proposal_id: str, root: str = "") -> None:
    """执行提案。

    Args:
        proposal_id: 提案 ID
        root: 项目根目录
    """
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    record = store.get_proposal(proposal_id)

    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return

    current_status = record.get("status", "pending")
    if current_status not in ("pending", "approved"):
        print(f"\n{WARNING_PREFIX} 提案当前状态为 {current_status}，无法执行\n")
        return

    # 检查风险等级
    proposal = record.get("proposal", {})
    risk = proposal.get("risk_level", "low")

    if risk == "high":
        print(f"\n{WARNING_PREFIX} 高风险提案需人工确认后再执行")
        print(f"  请先批准: /self-opt approve {proposal_id}\n")
        return

    print(f"\n🔄 正在执行提案 {proposal_id}...\n")

    result = await store.apply_proposal_async(proposal_id, root=root)

    if result.status == "success":
        print(f"{SUCCESS_PREFIX} 提案执行成功")
        print(f"  应用变更: {result.changes_applied} 个\n")
    elif result.status == "skipped":
        print(f"{WARNING_PREFIX} 提案跳过执行: {result.error}\n")
    else:
        print(f"{ERROR_PREFIX} 提案执行失败: {result.error}\n")


def cmd_self_opt_analyze() -> None:
    """触发运行分析并生成提案。"""
    from miniagent.core.self_opt.proposal_generator import ProposalGenerator

    print("\n🔍 正在分析运行日志...\n")

    generator = ProposalGenerator()
    saved_ids = generator.generate_and_save()

    if saved_ids:
        print(f"{SUCCESS_PREFIX} 生成 {len(saved_ids)} 个优化提案:\n")
        for pid in saved_ids:
            print(f"  - {pid}")
        print()
    else:
        print("📭 未发现问题，无需生成提案\n")


def cmd_self_opt_report(date: str | None = None) -> None:
    """查看运行分析报告。

    Args:
        date: 日期（默认今天）
    """
    import json
    from datetime import datetime, timezone
    from miniagent.core.self_opt.proposal_store import get_reports_dir

    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    reports_dir = get_reports_dir()
    report_file = reports_dir / f"runtime-{date}.json"

    if not report_file.exists():
        print(f"\n{WARNING_PREFIX} 报告不存在: {date}\n")
        return

    try:
        with report_file.open("r", encoding="utf-8") as f:
            report = json.load(f)

        print(f"\n📊 运行分析报告: {date}\n")
        print(f"  摘要: {report.get('summary', '无')}")
        print(f"  Trace 事件数: {report.get('trace_events_count', 0)}")
        print(f"  会话数: {report.get('sessions_count', 0)}")
        print()

        # 工具统计
        tools = report.get("tools", {})
        tool_stats = tools.get("tools", {})
        if tool_stats:
            print("  工具统计:")
            for name, stats in sorted(tool_stats.items(), key=lambda x: x[1].get("avg_ms", 0), reverse=True)[:5]:
                print(f"    - {name}: {stats.get('count', 0)}次, 平均{stats.get('avg_ms', 0)}ms, 成功率{stats.get('success_rate', 1):.1%}")
            print()

        # 慢工具
        slow_tools = tools.get("slow_tools", [])
        if slow_tools:
            print("  ⚠️ 慢工具:")
            for t in slow_tools:
                print(f"    - {t.get('name')}: 平均 {t.get('avg_ms')}ms")
            print()

        # 失败工具
        failed_tools = tools.get("failed_tools", [])
        if failed_tools:
            print("  ❌ 失败率高工具:")
            for t in failed_tools:
                print(f"    - {t.get('name')}: 成功率 {t.get('success_rate', 0):.1%}")
            print()

        # LLM 统计
        llm = report.get("llm", {})
        if llm.get("request_count"):
            tokens = llm.get("total_tokens", {})
            print("  LLM 统计:")
            print(f"    - 请求次数: {llm.get('request_count', 0)}")
            print(f"    - 总 tokens: prompt={tokens.get('prompt', 0)}, completion={tokens.get('completion', 0)}")
            print()

        # 问题标记
        issues = report.get("issues", [])
        if issues:
            print("  🔧 发现问题:")
            for issue in issues:
                severity = issue.get("severity", 1)
                icon = "🔴" if severity >= 3 else "🟡"
                print(f"    {icon} [{issue.get('type')}] {issue.get('tool') or issue.get('error_type') or ''}")
            print()

    except Exception as e:
        print(f"\n{ERROR_PREFIX} 读取报告失败: {e}\n")


def cmd_help(
    message_queue: Any,
    instance_id: int | None = None,
) -> None:
    """显示分类帮助信息。

    按功能分组展示所有可用命令（Markdown 表格，便于飞书 lark_md 渲染）。

    Args:
        message_queue: 消息队列管理器实例
        instance_id: 当前实例 ID（可选）
    """
    print(format_help_markdown(message_queue, instance_id))


__all__ = [
    "cmd_schedule",
    "format_schedule_command_usage",
    "sync_channel_router_to_session",
    "cmd_session_list",
    "cmd_session_switch",
    "cmd_session_create",
    "cmd_session_rename",
    "cmd_session_delete",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
    "format_help_markdown",
    "feishu_markdown_commands_enabled",
    "feishu_dot_commands_full_enabled",
    "format_session_command_usage",
    "format_queue_command_usage",
    "format_queue_abort_message",
    "format_test_command_usage",
    "format_kb_command_usage",
    "cmd_kb_list",
    "cmd_kb_mount",
    "cmd_kb_unmount",
    "cmd_kb_search",
    "cmd_kb_reload",
    "cmd_bind",
    "cmd_unbind",
    "cmd_instance_handler",
    "cmd_improve",
    "_get_last_qa_with_metadata",
    "_extract_improve_suggestions",
    "_has_quality_evaluation",
    # 自我优化命令
    "cmd_self_opt_status",
    "cmd_self_opt_proposals",
    "cmd_self_opt_show",
    "cmd_self_opt_approve",
    "cmd_self_opt_reject",
    "cmd_self_opt_apply",
    "cmd_self_opt_analyze",
    "cmd_self_opt_report",
]
