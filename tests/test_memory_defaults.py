"""Tests for process default memory bundle."""

from __future__ import annotations

import os

from miniagent.memory.defaults import (
    get_process_default_memory_bundle,
    reset_process_default_memory_bundle_for_tests,
    resolve_memory_dependencies,
)


def test_bundle_respects_mini_agent_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    reset_process_default_memory_bundle_for_tests()
    try:
        ms, al, ki = get_process_default_memory_bundle()
        assert getattr(ms, "_state_dir") == str(tmp_path)
        assert getattr(ki, "_state_dir") == str(tmp_path)
        assert os.path.normpath(getattr(al, "_base_dir")) == os.path.normpath(
            str(tmp_path / "memory")
        )
    finally:
        reset_process_default_memory_bundle_for_tests()


def test_resolve_prefers_store_bound_index(tmp_path) -> None:
    from miniagent.memory.activity_log import ActivityLogger
    from miniagent.memory.keyword_index import KeywordIndex
    from miniagent.memory.store import DefaultMemoryStore

    root = str(tmp_path / "state")
    ki = KeywordIndex(state_dir=root)
    ms = DefaultMemoryStore(state_dir=root, keyword_index=ki)
    al = ActivityLogger(base_dir=str(tmp_path / "state" / "memory"))
    rms, ral, rki = resolve_memory_dependencies(ms, al, None)
    assert rms is ms
    assert ral is al
    assert rki is ki
