"""history 归档与整轮尾部截断顺序。"""

from __future__ import annotations

import os

from miniagent.memory.history_archive import (
    diary_file_path,
    maybe_archive_old_turns,
    trim_history_tail_by_turns,
)
from tests.history_helpers import history_turn as _turn


def test_trim_history_tail_by_turns_removes_whole_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    hist: list[dict] = []
    for i in range(30):
        hist.extend(_turn(f"u{i}", f"a{i}"))
    assert len(hist) == 60
    guard = 0
    while len(hist) > 10 and guard < 500:
        trim_history_tail_by_turns(hist, cap=10)
        guard += 1
    assert len(hist) <= 10
    assert hist[0]["role"] == "user"
    assert "u" in hist[0]["content"]


def test_archive_before_trim_preserves_chunks_in_diary(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIAGENT_MEMORY_HISTORY_MAX_MESSAGES", "8")

    session_key = "test_sess"
    hist: list[dict] = []
    for i in range(20):
        hist.extend(_turn(f"user-{i}", f"reply-{i}"))

    g = 0
    while len(hist) > 8 and g < 500:
        maybe_archive_old_turns(session_key, hist)
        g += 1
    assert len(hist) <= 8

    path = diary_file_path(session_key)
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    assert "user-0" in raw or "reply-0" in raw

    g = 0
    while len(hist) > 6 and g < 500:
        trim_history_tail_by_turns(hist, cap=6)
        g += 1
    assert len(hist) <= 6


def test_archive_anchor_has_archive_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIAGENT_MEMORY_HISTORY_MAX_MESSAGES", "4")

    sk = "ref_sess"
    hist = _turn("a", "b") + _turn("c", "d") + _turn("e", "f")
    g = 0
    while len(hist) > 4 and g < 20:
        maybe_archive_old_turns(sk, hist)
        g += 1
    anchors = [m for m in hist if m.get("_history_archive_marker")]
    assert anchors
    assert "_archive_ref" in anchors[0]
    ref = anchors[0]["_archive_ref"]
    assert "seq" in ref and "diary_path" in ref
