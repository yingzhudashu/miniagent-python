"""Tests for cli_commands helpers (abort formatting, improve, history plaintext)."""

from __future__ import annotations

import json
from pathlib import Path

from miniagent.agent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.commands.session_management import (
    _extract_improve_suggestions,
    _get_last_qa_with_metadata,
    _has_quality_evaluation,
    _load_session_history_messages,
    build_session_history_plaintext,
    cmd_improve,
    format_queue_abort_message,
)


def test_format_queue_abort_message_idle() -> None:
    msg = format_queue_abort_message({})
    assert SUCCESS_PREFIX in msg
    assert "无运行中或排队" in msg


def test_format_queue_abort_message_cancelled_tasks() -> None:
    msg = format_queue_abort_message(
        {
            "cancelled_running": True,
            "cancelled_pending": 2,
            "cancelled_dispatch_wait": 1,
        }
    )
    assert "已取消正在执行" in msg
    assert "2 个排队" in msg
    assert "dispatch_wait" in msg


def test_format_queue_abort_message_preemptive_only() -> None:
    msg = format_queue_abort_message({"cancelled_preemptive_current": True})
    assert "preemptive" in msg
    assert "已取消正在执行" not in msg


class _StubSession:
    def __init__(
        self,
        *,
        conversation_history: list[dict] | None = None,
        files_path: str | None = None,
    ) -> None:
        self.conversation_history = conversation_history
        self.files_path = files_path


class _StubSessionManager:
    def __init__(self, session: _StubSession | None) -> None:
        self._session = session

    def get(self, _session_id: str) -> _StubSession | None:
        return self._session


def test_load_session_history_prefers_memory() -> None:
    mem = [{"role": "user", "content": "hi"}]
    session = _StubSession(conversation_history=mem, files_path="/tmp/ws/files")
    assert _load_session_history_messages(session) == mem


def test_load_session_history_falls_back_to_disk(tmp_path: Path) -> None:
    ws = tmp_path / "sess" / "files"
    ws.mkdir(parents=True)
    history = [
        {"role": "user", "content": "disk-q"},
        {"role": "assistant", "content": "disk-a"},
    ]
    (tmp_path / "sess" / "history.json").write_text(
        json.dumps(history), encoding="utf-8"
    )
    session = _StubSession(conversation_history=[], files_path=str(ws))
    assert _load_session_history_messages(session) == history


def test_build_session_history_plaintext_from_memory() -> None:
    session = _StubSession(
        conversation_history=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
    )
    sm = _StubSessionManager(session)
    text = build_session_history_plaintext(sm, "default")
    assert "You\nhello" in text
    assert "Assistant\nworld" in text


def test_get_last_qa_with_metadata() -> None:
    session = _StubSession(
        conversation_history=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "metadata": {"x": 1}},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
    )
    sm = _StubSessionManager(session)
    user, assistant = _get_last_qa_with_metadata(sm, "s1")
    assert user is not None and user.get("content") == "q2"
    assert assistant is not None and assistant.get("content") == "a2"


def test_extract_improve_suggestions_and_has_quality_evaluation() -> None:
    content = (
        "answer body\n"
        "---\n"
        "🤖 助手质量评分: 6/10\n\n"
        "建议：\n"
        "- 补充示例\n"
        "- 缩短段落\n"
    )
    msg = {"content": content}
    assert _has_quality_evaluation(msg)
    assert _extract_improve_suggestions(msg) == ["补充示例", "缩短段落"]


def test_cmd_improve_no_history() -> None:
    sm = _StubSessionManager(_StubSession(conversation_history=[]))
    msg, ok = cmd_improve(sm, "s1")
    assert ok is False
    assert WARNING_PREFIX in msg


def test_cmd_improve_with_suggestions() -> None:
    assistant_content = (
        "ans\n---\n🤖 质量评分 5/10\n\n建议：\n- fix typo\n"
    )
    session = _StubSession(
        conversation_history=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": assistant_content},
        ]
    )
    sm = _StubSessionManager(session)
    result = cmd_improve(sm, "s1")
    assert isinstance(result, tuple) and len(result) == 3
    user, assistant, suggestions = result
    assert user["content"] == "q"
    assert assistant["content"] == assistant_content
    assert suggestions == ["fix typo"]


def test_cmd_improve_passed_without_force() -> None:
    assistant_content = (
        "ans\n---\n🤖 质量评分 9/10\n\n建议：\n"
    )
    session = _StubSession(
        conversation_history=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": assistant_content},
        ]
    )
    sm = _StubSessionManager(session)
    msg, ok = cmd_improve(sm, "s1")
    assert ok is False
    assert SUCCESS_PREFIX in msg
    assert "无需改进" in msg


def test_cmd_improve_force_when_passed() -> None:
    assistant_content = (
        "ans\n---\n🤖 质量评分 9/10\n\n建议：\n"
    )
    session = _StubSession(
        conversation_history=[
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": assistant_content},
        ]
    )
    sm = _StubSessionManager(session)
    user, assistant, suggestions = cmd_improve(sm, "s1", force=True)
    assert user["content"] == "q"
    assert suggestions == []
