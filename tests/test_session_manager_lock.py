"""SessionManager 写路径锁测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.session.manager import DefaultSessionManager, SessionOptions


@pytest.fixture
def workspaces(tmp_path: Path) -> Path:
    ws = tmp_path / "sessions"
    ws.mkdir()
    with patch("miniagent.session.manager._get_workspaces_dir", return_value=str(ws)):
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
