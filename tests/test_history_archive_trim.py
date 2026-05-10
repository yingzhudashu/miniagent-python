"""history 归档与整轮尾部截断顺序。"""

from __future__ import annotations

import os

from miniagent.memory.history_archive import (
    diary_file_path,
    maybe_archive_old_turns,
    trim_history_tail_by_turns,
)


def _turn(user: str, assistant: str) -> list[dict]:
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def test_trim_history_tail_by_turns_removes_whole_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    hist: list[dict] = []
    for i in range(30):
        hist.extend(_turn(f"u{i}", f"a{i}"))
    assert len(hist) == 60
    trim_history_tail_by_turns(hist, cap=10)
    assert len(hist) <= 10
    assert hist[0]["role"] == "user"
    assert "u" in hist[0]["content"]


def test_archive_before_trim_preserves_chunks_in_diary(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    monkeypatch.setenv("MINI_AGENT_HISTORY_MAX_MESSAGES", "8")

    session_key = "test_sess"
    hist: list[dict] = []
    for i in range(20):
        hist.extend(_turn(f"user-{i}", f"reply-{i}"))

    maybe_archive_old_turns(session_key, hist)
    assert len(hist) <= 8

    path = diary_file_path(session_key)
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    assert "user-0" in raw or "reply-0" in raw

    trim_history_tail_by_turns(hist, cap=6)
    assert len(hist) <= 6


def test_archive_anchor_has_archive_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    monkeypatch.setenv("MINI_AGENT_HISTORY_MAX_MESSAGES", "4")

    sk = "ref_sess"
    hist = _turn("a", "b") + _turn("c", "d") + _turn("e", "f")
    maybe_archive_old_turns(sk, hist)
    anchors = [m for m in hist if m.get("_history_archive_marker")]
    assert anchors
    assert "_archive_ref" in anchors[0]
    ref = anchors[0]["_archive_ref"]
    assert "seq" in ref and "diary_path" in ref
