"""Tests for infrastructure audit improvements (container, metrics, perf_cache, locks)."""

from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path

import pytest

from miniagent.infrastructure import debug_ndjson
from miniagent.infrastructure.container import (
    bootstrap_default_factories,
    get_tool_monitor,
    get_tool_registry,
    reset_bootstrap_for_tests,
)
from miniagent.infrastructure.feishu_inbound_lock import (
    read_feishu_inbound_owner,
    release_feishu_inbound_owner,
    try_acquire_feishu_inbound_owner,
)
from miniagent.infrastructure.metrics import PerformanceMetrics, get_global_metrics, reset_global_metrics
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.perf_cache import (
    cached_json_serialize,
    clear_all_caches,
    get_compiled_pattern,
)
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.infrastructure.trace_stats import get_daily_trace_file_path, get_trace_file
from tests.config_helpers import install_test_config


@pytest.fixture(autouse=True)
def _reset_container_bootstrap() -> None:
    reset_bootstrap_for_tests()
    reset_global_metrics()
    clear_all_caches()
    yield
    reset_bootstrap_for_tests()
    reset_global_metrics()
    clear_all_caches()


def test_bootstrap_registers_default_factories() -> None:
    bootstrap_default_factories()
    reg = get_tool_registry()
    mon = get_tool_monitor()
    assert isinstance(reg, DefaultToolRegistry)
    assert isinstance(mon, DefaultToolMonitor)
    assert get_tool_registry() is reg
    assert get_tool_monitor() is mon


def test_bootstrap_is_idempotent() -> None:
    bootstrap_default_factories()
    first = get_tool_registry()
    bootstrap_default_factories()
    assert get_tool_registry() is first


def test_clear_container_forces_new_instance_after_rebootstrap() -> None:
    bootstrap_default_factories()
    first = get_tool_registry()
    reset_bootstrap_for_tests()
    bootstrap_default_factories()
    second = get_tool_registry()
    assert first is not second


def test_performance_metrics_thread_safe_append() -> None:
    metrics = PerformanceMetrics(enabled=True)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for i in range(100):
                metrics.add_record("op", float(i))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    summary = metrics.get_summary()
    assert summary["op"].count == 400


def test_get_global_metrics_singleton() -> None:
    a = get_global_metrics()
    b = get_global_metrics()
    assert a is b


def test_perf_cache_pattern_and_json() -> None:
    p1 = get_compiled_pattern(r"\d+")
    p2 = get_compiled_pattern(r"\d+")
    assert p1 is p2
    assert p1.search("abc123")

    s1 = cached_json_serialize({"a": 1})
    s2 = cached_json_serialize({"a": 1})
    assert s1 == s2


def test_feishu_inbound_lock_acquire_and_release(tmp_path: Path) -> None:
    ok, msg = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=1)
    assert ok, msg
    owner = read_feishu_inbound_owner(state_dir=str(tmp_path))
    assert owner is not None
    assert owner.get("alive") is True
    release_feishu_inbound_owner(state_dir=str(tmp_path))
    assert read_feishu_inbound_owner(state_dir=str(tmp_path)) is None


def test_feishu_inbound_lock_blocks_second_live_pid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "miniagent.infrastructure.feishu_inbound_lock.is_process_running",
        lambda pid: pid > 0,
    )
    ok, _ = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=1)
    assert ok
    monkeypatch.setattr(
        "miniagent.infrastructure.feishu_inbound_lock.os.getpid",
        lambda: 888_888,
    )
    ok2, msg2 = try_acquire_feishu_inbound_owner(state_dir=str(tmp_path), instance_id=2)
    assert not ok2
    assert "占用" in msg2


def test_trace_stats_daily_path_alias() -> None:
    path = get_daily_trace_file_path("2026-06-15")
    assert path.name == "trace-2026-06-15.jsonl"
    assert get_trace_file("2026-06-15") == path


def test_debug_ndjson_respects_reload_config(tmp_path: Path) -> None:
    install_test_config(tmp_path, {})
    importlib.reload(debug_ndjson)
    log_file = tmp_path / "debug-hot.log"
    debug_ndjson.agent_debug_log(hypothesis_id="H", location="t", message="before")
    assert not log_file.exists()

    install_test_config(
        tmp_path,
        {"debug": {"session_id": "hot", "log_path": str(log_file)}},
    )
    from miniagent.infrastructure.json_config import reload_config

    reload_config()
    debug_ndjson.agent_debug_log(hypothesis_id="H", location="t", message="after")
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "after"
