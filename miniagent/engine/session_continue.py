"""CLI ``--continue`` 会话状态持久化（与 shutdown / 正常退出共用）。

写入 ``channel-router.json`` 的 ``last_cli_session*`` 字段。读取与回退见
``miniagent.engine.init._resolve_continue_session_id`` 与
``ChannelRouter.load_cli_session_state``。

``/session switch`` 切换会话时也会更新同一字段，经
``cli_commands._save_cli_session_state_on_switch`` 调用 ``persist_cli_session_state``。
"""

from __future__ import annotations

import logging
from typing import Any

from miniagent.engine.cli_state import CliLoopState
from miniagent.runtime.context import RuntimeContext

_logger = logging.getLogger(__name__)


def persist_cli_session_state(
    session_manager: Any,
    session_id: str,
    channel_router: Any | None,
    *,
    log_errors: bool = True,
) -> bool:
    """将会话元数据写入 channel router（``--continue`` 持久化核心逻辑）。

    Args:
        session_manager: 会话管理器；需支持 ``list_all_sessions_with_info()``
        session_id: 要保存的会话 ID
        channel_router: 通道路由器；为 ``None`` 时不写盘
        log_errors: 异常时是否记录 ``debug`` 日志

    Returns:
        ``True`` 若成功写入；``False`` 若跳过（空 id、无 router/manager、列表中无匹配）或失败。

    会话在列表中找不到时**不会**清除磁盘上已有的 ``last_cli_session``；下次启动时
    ``_resolve_continue_session_id`` 会检测已删除会话并回退。
    """
    if not session_id or not channel_router or not session_manager:
        return False
    try:
        from miniagent.session.manager import session_info_id, session_info_number

        sessions = session_manager.list_all_sessions_with_info()
        for s in sessions:
            if session_info_id(s) == session_id:
                session_number = session_info_number(s)
                session_title = s.get("title", "")
                channel_router.save_cli_session_state(
                    session_id,
                    session_number,
                    session_title,
                )
                return True
        return False
    except Exception as e:
        if log_errors:
            _logger.debug("保存 CLI 会话状态失败: %s", e)
        return False


def save_cli_session_state(ctx: RuntimeContext, state: CliLoopState) -> None:
    """保存 CLI 上次活跃会话到 ``channel-router.json``（``--continue`` 功能）。

    供 ``shutdown_runtime`` 与 ``run_cli_loop`` 正常退出路径调用；失败时不抛异常，
    以免阻塞进程退出。

    Args:
        ctx: 运行时上下文（使用 ``ctx.channel_router``）
        state: CLI 循环状态（使用 ``active_session_id`` 与 ``session_manager``）

    以下情况静默跳过（不写盘）：

    - ``active_session_id`` 为空
    - ``session_manager`` 为 ``None``
    - 活跃 ID 不在 ``list_all_sessions_with_info()`` 结果中
    """
    persist_cli_session_state(
        state.get("session_manager"),
        state.get("active_session_id", ""),
        ctx.channel_router,
    )


__all__ = ["persist_cli_session_state", "save_cli_session_state"]
