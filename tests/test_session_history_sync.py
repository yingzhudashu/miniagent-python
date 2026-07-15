"""回归：Session.conversation_history 与 ctx 引用同步，history.json 正确落盘。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from miniagent.engine.cli_transcript import history_loaded_end
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
    assert saved["schema_version"] == 2
    assert saved["message_format"] == "miniagent-conversation-v1"
    assert len(saved["messages"]) == 2
    assert saved["messages"][0]["role"] == "user"
    assert saved["messages"][1]["role"] == "assistant"

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
    assert len(saved["messages"]) == 2
    assert saved["messages"] == session.conversation_history


def test_load_range_expands_assistant_window_to_preserve_user_turn(
    session_manager: DefaultSessionManager,
) -> None:
    """窗口首条为 assistant 时，向前补入对应 user，CLI 计数仍推进实际条数。"""
    session_id = "range-expand"
    session = session_manager.get_or_create(session_id, SessionOptions(description="test"))
    session.conversation_history.extend(
        [
            {"role": "user", "content": "q0"},
            {"role": "assistant", "content": "a0"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
    )

    messages, total = session_manager.load_session_history_range(session_id, start_idx=0, count=1)

    assert total == 4
    assert messages == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert history_loaded_end(0, len(messages), total) == 2


def test_consecutive_history_ranges_do_not_skip_or_duplicate_turns(
    session_manager: DefaultSessionManager,
) -> None:
    """连续懒加载批次可组合为完整旧到新的问答序列。"""
    session_id = "range-contiguous"
    session = session_manager.get_or_create(session_id, SessionOptions(description="test"))
    expected = []
    for i in range(4):
        expected.extend(
            [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": f"a{i}"},
            ]
        )
    session.conversation_history.extend(expected)

    latest, total = session_manager.load_session_history_range(session_id, start_idx=0, count=2)
    first_loaded_end = history_loaded_end(0, len(latest), total)
    older, total = session_manager.load_session_history_range(
        session_id,
        start_idx=first_loaded_end,
        count=2,
    )

    assert latest == expected[-2:]
    assert older == expected[-4:-2]
    assert older + latest == expected[-4:]
    assert history_loaded_end(first_loaded_end, len(older), total) == 4


def test_load_range_preserves_long_assistant_content_on_disk(
    session_manager: DefaultSessionManager,
) -> None:
    """显示层截断策略不应改变 history.json 中的长答案内容。"""
    session_id = "long-answer"
    session = session_manager.get_or_create(session_id, SessionOptions(description="test"))
    long_answer = "长答案" * 1000
    session.conversation_history.extend(
        [
            {"role": "user", "content": "请详细回答"},
            {"role": "assistant", "content": long_answer},
        ]
    )

    messages, total = session_manager.load_session_history_range(session_id, start_idx=0, count=2)
    session_manager.save_session_history(session_id)

    assert total == 2
    assert messages[-1]["content"] == long_answer
    with open(_history_path(session_manager, session_id), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["messages"][-1]["content"] == long_answer


def test_restore_truncates_large_disk_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """磁盘 history.json 超过 max_history_messages 时，恢复后内存应截断。"""
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))

    def _cfg(key: str, default=None):
        if key == "memory.max_history_messages":
            return 50
        from miniagent.infrastructure.json_config import get_config as real_get_config

        return real_get_config(key, default)

    monkeypatch.setattr("miniagent.session.manager.get_config", _cfg)

    sm = DefaultSessionManager(DefaultToolRegistry())
    session_id = "big-history"
    sm.get_or_create(session_id, SessionOptions(description="seed"))
    ctx = sm._sessions[session_id]
    workspace = ctx["config"].workspace_path

    disk_data: list[dict[str, str]] = []
    for i in range(120):
        disk_data.append({"role": "user", "content": f"u-{i}"})
        disk_data.append({"role": "assistant", "content": f"a-{i}"})

    path = os.path.join(workspace, "history.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(disk_data, f)

    # 驱逐内存后从磁盘恢复
    del sm._sessions[session_id]
    restored = sm.get_or_create(session_id, SessionOptions(description="restore"))
    assert len(restored.conversation_history) <= 50
    assert restored.conversation_history[-1]["content"] == "a-119"
