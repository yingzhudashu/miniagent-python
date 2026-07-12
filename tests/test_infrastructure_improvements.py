"""Tests for infrastructure metrics, caches and process locks."""

from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path

from miniagent.infrastructure import debug_ndjson
from miniagent.infrastructure.feishu_inbound_lock import (
    read_feishu_inbound_owner,
    release_feishu_inbound_owner,
    try_acquire_feishu_inbound_owner,
)
from miniagent.infrastructure.metrics import PerformanceMetrics
from tests.config_helpers import install_test_config


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


def test_performance_metrics_retains_exact_totals_after_sample_eviction(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "miniagent.infrastructure.metrics.get_config",
        lambda key, default: {
            "debug.perf_metrics_max_records": 100,
            "debug.perf_metrics_max_names": 8,
        }.get(key, default),
    )
    metrics = PerformanceMetrics(enabled=True)
    metrics.add_record("rare", 25.0)
    for _ in range(150):
        metrics.add_record("busy", 1.0)

    summary = metrics.get_summary()

    assert summary["rare"].count == 1
    assert summary["rare"].avg_ms == 25.0
    assert summary["rare"].p50_ms == 25.0


def test_performance_metrics_bounds_metric_names(monkeypatch) -> None:
    monkeypatch.setattr(
        "miniagent.infrastructure.metrics.get_config",
        lambda key, default: {
            "debug.perf_metrics_max_records": 100,
            "debug.perf_metrics_max_names": 2,
        }.get(key, default),
    )
    metrics = PerformanceMetrics(enabled=True)
    for index in range(10):
        metrics.add_record(f"metric-{index}", float(index))

    summary = metrics.get_summary()

    assert set(summary) == {"metric-0", "__other__"}
    assert summary["__other__"].count == 9


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
