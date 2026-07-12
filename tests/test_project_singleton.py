"""Tests for project singleton bootstrap (__main__._bootstrap_project_paths)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from miniagent.__main__ import _bootstrap_project_paths
from miniagent.infrastructure.instance import reset_instance_registry_for_tests
from miniagent.infrastructure.json_config import reset_config_loader
from miniagent.infrastructure.paths import resolve_project_key
from tests.config_helpers import install_test_config


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reset_config_loader()
    monkeypatch.delenv("MINIAGENT_PROJECT_DIR", raising=False)
    monkeypatch.delenv("MINIAGENT_PATHS_STATE_DIR", raising=False)
    monkeypatch.delenv("MINIAGENT_CONTINUE_SESSION", raising=False)
    monkeypatch.delenv("MINIAGENT_REGISTRY_STATE_DIR", raising=False)
    reg = tmp_path / "registry"
    reg.mkdir()
    monkeypatch.setenv("MINIAGENT_REGISTRY_STATE_DIR", str(reg))
    install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
    reset_instance_registry_for_tests()
    yield
    reset_instance_registry_for_tests()
    reset_config_loader()


def _norm_dir(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.realpath(str(path))))


def test_bootstrap_sets_project_dir_and_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    _bootstrap_project_paths(skip_continue=True)

    assert os.environ["MINIAGENT_PROJECT_DIR"] == _norm_dir(project)
    reg = os.environ["MINIAGENT_REGISTRY_STATE_DIR"]
    key = resolve_project_key(str(project))
    expected = os.path.join(reg, "projects", key)
    assert os.environ["MINIAGENT_PATHS_STATE_DIR"] == expected


def test_bootstrap_respects_existing_paths_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    custom = str(tmp_path / "custom-ws")
    monkeypatch.chdir(project)
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", custom)

    _bootstrap_project_paths(skip_continue=True)

    assert os.environ["MINIAGENT_PATHS_STATE_DIR"] == custom


def test_bootstrap_auto_continue_when_no_alive_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    _bootstrap_project_paths()

    assert os.environ.get("MINIAGENT_CONTINUE_SESSION") == "1"


def test_bootstrap_skip_continue_does_not_set_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    _bootstrap_project_paths(skip_continue=True)

    assert not os.environ.get("MINIAGENT_CONTINUE_SESSION")


def test_bootstrap_conflict_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    reg = os.environ["MINIAGENT_REGISTRY_STATE_DIR"]
    other_pid = 99001

    def checker(pid: int) -> bool:
        return pid == other_pid

    monkeypatch.setattr(
        "miniagent.infrastructure.instance.is_process_running",
        checker,
    )
    stale = Path(reg) / "instances" / "1"
    stale.mkdir(parents=True)
    meta = {
        "pid": other_pid,
        "instance_id": 1,
        "mode": "cli",
        "active_sessions": [],
        "hostname": "h",
        "start_time": "2026-05-09T10:00:00",
        "project_dir": str(project),
    }
    (stale / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        _bootstrap_project_paths(skip_continue=True)
    assert exc.value.code == 2


def test_bootstrap_for_stop_skips_conflict_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    reg = os.environ["MINIAGENT_REGISTRY_STATE_DIR"]
    other_pid = 99002

    def checker(pid: int) -> bool:
        return pid == other_pid

    monkeypatch.setattr(
        "miniagent.infrastructure.instance.is_process_running",
        checker,
    )
    stale = Path(reg) / "instances" / "1"
    stale.mkdir(parents=True)
    meta = {
        "pid": other_pid,
        "instance_id": 1,
        "mode": "cli",
        "active_sessions": [],
        "hostname": "h",
        "start_time": "2026-05-09T10:00:00",
        "project_dir": str(project),
    }
    (stale / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    _bootstrap_project_paths(for_stop=True)
    assert os.environ["MINIAGENT_PROJECT_DIR"] == _norm_dir(project)
