"""Active session selector for the TUI."""

from __future__ import annotations

from typing import Any


async def choose_session(state: Any) -> str | None:
    """展示未销毁会话的选择对话框。"""
    manager = state.get("session_manager") if isinstance(state, dict) else None
    if manager is None:
        return None
    sessions = manager.list()
    if not sessions:
        return None
    from prompt_toolkit.shortcuts import radiolist_dialog

    values = [
        (
            session.id,
            f"{session.id} · {session.description or '未命名'} · {session.turn_count} 轮",
        )
        for session in sessions
        if not session.destroyed
    ]
    dialog = radiolist_dialog(
        title="切换会话",
        text="选择已有会话；未完成的当前请求不会被迁移。",
        values=values,
        ok_text="切换",
        cancel_text="取消",
    )
    return await dialog.run_async()


__all__ = ["choose_session"]
