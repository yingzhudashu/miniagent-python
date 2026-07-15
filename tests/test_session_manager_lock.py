"""SessionManager 写路径锁测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
from miniagent.assistant.session.manager import DefaultSessionManager, SessionOptions


@pytest.fixture
def workspaces(tmp_path: Path) -> Path:
    ws = tmp_path / "sessions"
    ws.mkdir()
    with patch("miniagent.assistant.session.manager._get_workspaces_dir", return_value=str(ws)):
        yield ws


@pytest.mark.asyncio
async def test_concurrent_save_history_async(workspaces: Path) -> None:
    registry = DefaultToolRegistry()
    mgr = DefaultSessionManager(registry)
    sid = "lock-test"
    ctx = mgr.get_or_create(sid, SessionOptions(description="test"))
    ctx.conversation_history.append({"role": "user", "content": "hello"})

    await asyncio.gather(
        mgr.save_session_history_async(sid),
        mgr.save_session_history_async(sid),
    )
    mgr.save_session_history(sid)

    loaded = mgr.load_session_history(sid)
    assert len(loaded) >= 1
    assert loaded[0]["content"] == "hello"


def test_destroy_without_files_skips_redundant_config_write(workspaces: Path) -> None:
    mgr = DefaultSessionManager(DefaultToolRegistry())
    sid = "ephemeral"
    session = mgr.get_or_create(sid, SessionOptions(description="test"))
    workspace = Path(mgr._sessions[sid]["config"].workspace_path)
    assert session is not None

    with patch.object(mgr, "_save_config") as save_config:
        assert mgr.destroy(sid, keep_files=False) is True

    save_config.assert_not_called()
    assert not workspace.exists()


def test_forget_session_only_detaches_in_memory(workspaces: Path) -> None:
    mgr = DefaultSessionManager(DefaultToolRegistry())
    sid = "background-session"
    mgr.get_or_create(sid, SessionOptions(description="test"))
    workspace = Path(mgr._sessions[sid]["config"].workspace_path)

    assert mgr.forget_session(sid) is True
    assert mgr.get(sid) is None
    assert workspace.exists()
