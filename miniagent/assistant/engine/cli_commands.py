"""CLI 命令处理模块

本模块包含 CLI 斜杠命令的核心实现，从 unified.py 拆分而来。

本模块直接实现或以兼容导入聚合：
- 会话管理：列出、切换、创建、重命名、删除会话
- 消息队列：查看队列状态、切换队列模式
- 通道路由：/session switch 同步 CLI 与自动私聊绑定
- 定时任务：/schedule add/list/remove/enable/disable
- 帮助显示：分类展示所有可用命令
- 答案改进命令

以下命令在 ``miniagent/engine/commands/`` 子包实现，本模块作为 CLI 命令聚合入口导入：
- kb_commands: 知识库命令（cmd_kb_*）
- instance_commands: 实例管理（cmd_instance_handler）
- config_commands: 配置检查与用法辅助（feishu_*_enabled、format_test_command_usage）
- self_opt_commands: 自我优化提案查询、审批、执行与报告（cmd_self_opt_*）

注意：所有会话命令同时支持**编号**（如 1）和**原始 ID**（如 default）。

终端帮助正文与列表格式维护请与 ``docs/CLI.md`` 对齐。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

_logger = logging.getLogger(__name__)

# 性能优化：预编译高频正则表达式
_QUALITY_EVAL_SUGGESTIONS_PATTERN = re.compile(
    r"---\n🤖 .*?质量评分.*?\n\n建议：\n((?:- .+\n?)+)"
)

# 从命令子模块导入
from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.commands.config_commands import (
    feishu_dot_commands_full_enabled,
    feishu_markdown_commands_enabled,
    format_test_command_usage,
)
from miniagent.assistant.engine.commands.help_commands import format_help_markdown
from miniagent.assistant.engine.commands.instance_commands import cmd_instance_handler
from miniagent.assistant.engine.commands.kb_commands import (
    cmd_kb_list,
    cmd_kb_mount,
    cmd_kb_reload,
    cmd_kb_search,
    cmd_kb_unmount,
    format_kb_command_usage,
)
from miniagent.assistant.engine.commands.queue_commands import (
    _md_escape_cell,
    cmd_queue_set,
    cmd_queue_status,
    format_queue_abort_message,
    format_queue_command_usage,
)
from miniagent.assistant.engine.commands.schedule_commands import (
    cmd_schedule,
    format_schedule_command_usage,
)


def format_session_command_usage() -> str:
    """与 ``format_help_markdown`` 中会话小节一致的用法说明（CLI 提示与 dispatch 共用）。"""
    return (
        "用法:\n"
        "  /session list                   列出所有会话\n"
        "  /session switch <编号/ID>       切换到指定会话（含 oc_xxx 飞书群；飞书默认仅 list）\n"
        "  /session create <ID> [标题]     创建新会话\n"
        "  /session rename <编号/ID> <标题>  重命名会话\n"
        "  /session delete <编号/ID>       删除指定会话（不能删除当前活跃会话）"
    )


def sync_channel_router_to_session(
    channel_router: Any,
    session_id: str,
    feishu_p2p_synced_senders: set[str] | None,
) -> None:
    """将 CLI 与「自动同步」的飞书私聊通道绑定到同一主会话，并更新 primary。"""
    from miniagent.assistant.infrastructure.channel_router import ChannelRouter
    from miniagent.assistant.infrastructure.cli_feishu_policy import (
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
    from miniagent.assistant.engine.session_continue import persist_cli_session_state

    persist_cli_session_state(session_manager, session_id, channel_router)


def _load_session_history_messages(session: Any) -> list[Any]:
    """从会话对象加载对话历史（内存 ``conversation_history`` 优先，回退 ``history.json``）。"""
    history = getattr(session, "conversation_history", None) or []
    if history:
        return history

    files_path = getattr(session, "workspace_path", None) or getattr(session, "files_path", None)
    if not files_path:
        return []

    history_path = os.path.join(os.path.dirname(files_path), "history.json")
    if not os.path.isfile(history_path):
        return []

    try:
        with open(history_path, encoding="utf-8-sig") as f:
            loaded = json.load(f)
    except Exception:
        return []

    return loaded if isinstance(loaded, list) else []


def _get_last_qa_with_metadata(
    session_manager: Any,
    session_id: str,
) -> tuple[dict | None, dict | None]:
    """获取当前会话的最后一轮 Q&A（连续 user → assistant 对，带 metadata）。

    用于 .improve 和 .review 命令获取上一轮对话上下文。

    Args:
        session_manager: 会话管理器实例
        session_id: 当前会话 ID

    Returns:
        (user_msg_dict, assistant_msg_dict)
        消息字典包含 role、content、metadata 等字段。
    """
    session = session_manager.get(session_id)
    if session is None:
        return None, None

    history = _load_session_history_messages(session)

    assistant_idx = -1
    last_assistant: dict | None = None
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
            last_assistant = msg
            assistant_idx = i
            break

    if last_assistant is None:
        return None, None

    last_user: dict | None = None
    for i in range(assistant_idx - 1, -1, -1):
        msg = history[i]
        if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("content"):
            last_user = msg
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
    return [
        line[2:].strip()
        for line in suggestions_block.strip().split("\n")
        if line.startswith("- ")
    ]


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
    该方法支持内存和磁盘双重查找。非数字 ID 会经 ``normalize_bind_session_id``
    规范化（``oc_*`` → ``feishu:oc_*``）；飞书群会话在尚未落盘时也可解析，
    供 ``/session switch`` 创建占位会话并聚焦 CLI。

    Args:
        session_manager: 会话管理器实例
        id_or_number: 用户输入，如 "1"（编号）或 "default"（原始 ID）

    Returns:
        解析后的 session_id，找不到返回 None
    """
    from miniagent.assistant.infrastructure.cli_feishu_policy import (
        is_feishu_group_session,
        normalize_bind_session_id,
    )

    raw = (id_or_number or "").strip()
    if not raw:
        return None

    def _lookup(candidate: str) -> str | None:
        if hasattr(session_manager, "resolve_session_id"):
            sid = session_manager.resolve_session_id(candidate)
            if sid:
                return sid
        if session_manager.get(candidate):
            return candidate
        return None

    if raw.isdigit():
        found = _lookup(raw)
        if found:
            return found
        num = int(raw)
        for s in session_manager.list_all_sessions_with_info():
            if s.get("number") == num:
                return s.get("id") or s.get("session_id")
        return None

    normalized = normalize_bind_session_id("cli", raw)
    for candidate in (normalized, raw):
        if not candidate:
            continue
        found = _lookup(candidate)
        if found:
            return found

    if is_feishu_group_session(normalized):
        return normalized

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
        markdown: True 时输出 GFM 表格（由 ``feishu.markdown_commands`` 或
            ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS=1`` 启用）
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
    5. 更新活跃会话 ID 并同步通道路由

    Args:
        session_manager: 会话管理器实例
        active_session_id: 当前活跃会话 ID
        id_or_number: 目标会话编号（如 1）或原始 ID
        try_lock_session_async: 异步尝试获取会话锁的函数
        release_session_lock: 释放会话锁的函数
        is_session_locked: 检查会话是否被锁定的函数
        channel_router: 通道路由器（可选，用于 CLI/飞书私聊绑定同步）
        feishu_p2p_synced_senders: 已自动跟随 CLI 的飞书私聊 sender 集合（可选）

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
        _logger.debug("加载目标会话失败: %s", e)

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

    from miniagent.assistant.session.manager import SessionOptions

    # 创建会话配置
    session_opts = SessionOptions(
        title=title or "",
        description=title or session_id,
    )
    session_manager.get_or_create(session_id, session_opts)

    ok, reason = await try_lock_session_async(session_id)
    if not ok:
        print(f"{ERROR_PREFIX} 会话已创建但加锁失败: {reason}")
        return

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
        _logger.debug("释放会话锁失败: %s", e)

    ok = session_manager.destroy(session_id, keep_files=keep_files)
    if ok:
        action = "已删除（保留文件）" if keep_files else "已删除（清除文件）"
        print(f"{SUCCESS_PREFIX} {display} {action}")
    else:
        print(f"{ERROR_PREFIX} 删除失败: {display}")


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
    if not last_user or not last_assistant:
        return f"{WARNING_PREFIX} 当前会话无历史对话，无法改进", False

    # 3. 提取质量评估建议
    suggestions = _extract_improve_suggestions(last_assistant)

    if not suggestions:
        if not _has_quality_evaluation(last_assistant):
            return f"{WARNING_PREFIX} 上一轮未启用质量评估，无法改进", False
        if force:
            # 强制改进模式：即使无建议也允许改进（返回空建议列表）
            return last_user, last_assistant, []
        return f"{SUCCESS_PREFIX} 上一轮质量评估已通过，无需改进（使用 `/improve --force` 强制改进）", False

    # 4. 检查是否已改进过（限制轮次）
    metadata = last_assistant.get("metadata", {})
    if metadata.get("improved") and not reset:
        improve_round = metadata.get("improve_round", 1)
        if improve_round >= 3:
            return f"{WARNING_PREFIX} 已达到改进轮次上限（3轮），建议重新提问或使用 `/review`", False

    # 5. 返回改进所需的上下文
    return last_user, last_assistant, suggestions


def build_session_history_plaintext(session_manager: Any, session_id: str) -> str:
    """拼接 user/assistant 纯文本（简易 CLI ``/copy`` 用）。

    优先使用内存 ``conversation_history``，否则回退 ``history.json``。
    """
    if not session_manager or not session_id:
        return ""

    session = session_manager.get(session_id)
    if session is None:
        return ""

    messages = _load_session_history_messages(session)
    if not messages:
        return ""

    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        label = "You" if role == "user" else "Assistant"
        parts.append(f"{label}\n{content}")

    return "\n\n".join(parts)




# 保留历史导入路径，便于第三方扩展平滑迁移。
from miniagent.assistant.engine.commands.self_opt_commands import (
    cmd_self_opt_analyze,
    cmd_self_opt_apply,
    cmd_self_opt_approve,
    cmd_self_opt_proposals,
    cmd_self_opt_reject,
    cmd_self_opt_report,
    cmd_self_opt_show,
    cmd_self_opt_status,
)


def cmd_help(
    message_queue: Any,
    instance_id: int | None = None,
) -> None:
    """显示分类帮助信息。

    按功能分组展示所有可用命令（Markdown 列表，便于飞书 lark_md 渲染）。

    Args:
        message_queue: 消息队列管理器实例
        instance_id: 当前实例 ID（可选）
    """
    print(format_help_markdown(message_queue, instance_id))


# improve 辅助函数对外导出，供 command_dispatch 与测试复用
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
    "build_session_history_plaintext",
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
