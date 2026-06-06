"""Tests for process default memory bundle."""

from __future__ import annotations

import os

import pytest

from miniagent.memory.defaults import (
    get_process_default_memory_bundle,
    reset_process_default_memory_bundle_for_tests,
    resolve_memory_dependencies,
)
from tests.config_helpers import install_test_config


def test_bundle_respects_state_dir_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = str(tmp_path)
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)
    install_test_config(tmp_path, {"paths": {"state_dir": state_dir}})
    reset_process_default_memory_bundle_for_tests()
    try:
        ms, al, ki = get_process_default_memory_bundle()
        assert getattr(ms, "_state_dir") == state_dir
        assert getattr(ki, "_state_dir") == state_dir
        assert os.path.normpath(getattr(al, "_base_dir")) == os.path.normpath(
            os.path.join(state_dir, "memory")
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
