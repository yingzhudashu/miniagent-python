"""回归：Session.conversation_history 与 ctx 引用同步，history.json 正确落盘。"""

from __future__ import annotations

import json
import os

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.session.manager import DefaultSessionManager
from miniagent.types.memory import SessionOptions


@pytest.fixture
def session_manager(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    sm = DefaultSessionManager(DefaultToolRegistry())
    yield sm


def _history_path(sm: DefaultSessionManager, session_id: str) -> str:
    ctx = sm._sessions[session_id]
    return os.path.join(ctx["config"].workspace_path, "history.json")


def test_load_range_then_append_persists_history(session_manager: DefaultSessionManager) -> None:
    """模拟 CLI 启动 load_range 后引擎追加历史，save 应写入非空 history.json。"""
    session_id = "default"
    session = session_manager.get_or_create(session_id, SessionOptions(description="test"))
    ctx = session_manager._sessions[session_id]

    # CLI 启动时会调用 load_session_history_range；旧实现此处会分裂引用
    messages, total = session_manager.load_session_history_range(session_id, start_idx=0, count=10)
    assert messages == []
    assert total == 0

    # 引擎通过 Session 对象追加（与 UnifiedEngine 行为一致）
    session.conversation_history.append({"role": "user", "content": "你好"})
    session.conversation_history.append({"role": "assistant", "content": "你好！"})

    session_manager.save_session_history(session_id)

    path = _history_path(session_manager, session_id)
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 2
    assert saved[0]["role"] == "user"
    assert saved[1]["role"] == "assistant"

    # ctx 与 session 仍指向同一 list
    assert ctx["conversation_history"] is session.conversation_history


def test_load_range_loads_disk_when_memory_empty(session_manager: DefaultSessionManager) -> None:
    """内存为空且磁盘有历史时，load_range 应加载并同步引用。"""
    session_id = "disk-restore"
    session_manager.get_or_create(session_id, SessionOptions(description="test"))
    ctx = session_manager._sessions[session_id]

    disk_data = [
        {"role": "user", "content": "旧消息"},
        {"role": "assistant", "content": "旧回复"},
    ]
    path = _history_path(session_manager, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk_data, f)

    messages, total = session_manager.load_session_history_range(session_id, start_idx=0, count=10)
    assert total == 2
    assert len(messages) == 2
    assert ctx["conversation_history"] is session_manager.get(session_id).conversation_history
    assert ctx["conversation_history"] == disk_data


def test_save_after_truncate_keeps_session_and_ctx_in_sync(
    session_manager: DefaultSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """截断后 save 应同步 Session 与 ctx，落盘内容与内存一致。"""
    import miniagent.session.manager as sm_mod

    def _truncate_tail(history: list, max_messages: int = 200) -> list:
        return list(history[-2:])

    monkeypatch.setattr(sm_mod, "_truncate_history", _truncate_tail)

    session_id = "truncate-test"
    session = session_manager.get_or_create(session_id, SessionOptions(description="test"))
    ctx = session_manager._sessions[session_id]

    for i in range(3):
        session.conversation_history.append({"role": "user", "content": f"q{i}"})
        session.conversation_history.append({"role": "assistant", "content": f"a{i}"})

    session_manager.save_session_history(session_id)

    assert ctx["conversation_history"] is session.conversation_history
    path = _history_path(session_manager, session_id)
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 2
    assert saved == session.conversation_history
