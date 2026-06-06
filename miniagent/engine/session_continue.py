"""CLI ``--continue`` 会话状态持久化（与 shutdown / 正常退出共用）。"""

from __future__ import annotations

from miniagent.engine.cli_state import CliLoopState
from miniagent.runtime.context import RuntimeContext


def save_cli_session_state(ctx: RuntimeContext, state: CliLoopState) -> None:
    """保存 CLI 上次活跃会话到 ``channel-router.json``（``--continue`` 功能）。"""
    try:
        session_id = state.get("active_session_id", "")
        if not session_id:
            return

        session_manager = state.get("session_manager")
        if not session_manager:
            return

        from miniagent.session.manager import session_info_id, session_info_number

        sessions = session_manager.list_all_sessions_with_info()
        for s in sessions:
            if session_info_id(s) == session_id:
                session_number = session_info_number(s)
                session_title = s.get("title", "")
                ctx.channel_router.save_cli_session_state(
                    session_id,
                    session_number,
                    session_title,
                )
                return
    except Exception:
        pass


__all__ = ["save_cli_session_state"]
