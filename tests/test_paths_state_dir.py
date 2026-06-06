"""Tests for paths.state_dir resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from miniagent.infrastructure.json_config import JsonConfigLoader
from miniagent.infrastructure.paths import (
    normalize_project_dir,
    paths_equal,
    resolve_legacy_cwd_state_dir,
    resolve_project_dir,
    resolve_project_key,
    resolve_project_root,
    resolve_project_state_dir,
    resolve_registry_state_dir,
    resolve_state_dir,
)
from tests.config_helpers import install_test_config


@pytest.fixture(autouse=True)
def _reset_loader():
    JsonConfigLoader._instance = None
    yield
    JsonConfigLoader._instance = None


@pytest.fixture
def registry_root(tmp_path, monkeypatch):
    reg = tmp_path / "registry"
    reg.mkdir()
    monkeypatch.setenv("MINIAGENT_REGISTRY_STATE_DIR", str(reg))
    return reg


class TestResolveStateDir:
    def test_default_uses_registry_projects_namespace(
        self, tmp_path, monkeypatch, registry_root
    ):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        project = tmp_path / "myapp"
        project.mkdir(parents=True)
        monkeypatch.chdir(project)

        key = resolve_project_key(str(project))
        expected = os.path.join(str(registry_root), "projects", key)
        assert resolve_state_dir() == expected
        assert resolve_project_state_dir() == expected

    def test_project_key_stable_and_distinct(self, tmp_path):
        a = tmp_path / "proj-a"
        b = tmp_path / "proj-b"
        a.mkdir()
        b.mkdir()
        key_a = resolve_project_key(str(a))
        key_b = resolve_project_key(str(b))
        assert key_a == resolve_project_key(str(a))
        assert key_a != key_b
        assert key_a.endswith("-") is False
        assert len(key_a.split("-")[-1]) == 8

    def test_absolute_path_uses_projects_subdir(self, tmp_path, monkeypatch):
        abs_dir = str(tmp_path / "custom-state")
        install_test_config(tmp_path, {"paths": {"state_dir": abs_dir}})
        monkeypatch.chdir(tmp_path)
        key = resolve_project_key(str(tmp_path))
        assert resolve_state_dir() == os.path.join(abs_dir, "projects", key)

    def test_env_overrides_config(self, tmp_path, monkeypatch):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        env_dir = str(tmp_path / "from-env")
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", env_dir)
        assert resolve_state_dir() == env_dir

    def test_legacy_cwd_fallback_when_sessions_exist(
        self, tmp_path, monkeypatch, registry_root
    ):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        project = tmp_path / "legacy_proj"
        legacy_ws = project / "workspaces"
        (legacy_ws / "sessions").mkdir(parents=True)
        monkeypatch.chdir(project)

        assert os.path.normcase(resolve_project_state_dir()) == os.path.normcase(str(legacy_ws))

    def test_legacy_registry_root_when_cwd_is_repo_root(
        self, tmp_path, monkeypatch, registry_root
    ):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        (registry_root / "sessions").mkdir(parents=True)
        repo_root = resolve_project_root()
        monkeypatch.chdir(repo_root)
        monkeypatch.setenv("MINIAGENT_PROJECT_DIR", repo_root)

        assert paths_equal(resolve_project_dir(), repo_root)
        assert resolve_project_state_dir() == str(registry_root)

    def test_existing_projects_dir_takes_priority_over_legacy_cwd(
        self, tmp_path, monkeypatch, registry_root
    ):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        project = tmp_path / "proj"
        project.mkdir()
        legacy_ws = project / "workspaces"
        (legacy_ws / "sessions").mkdir(parents=True)
        monkeypatch.chdir(project)

        key = resolve_project_key(str(project))
        new_path = registry_root / "projects" / key
        new_path.mkdir(parents=True)

        assert resolve_project_state_dir() == str(new_path)

    def test_legacy_cwd_differs_from_registry(self, tmp_path, monkeypatch):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        legacy_cwd = tmp_path / "legacy_cwd"
        legacy_cwd.mkdir(parents=True, exist_ok=True)
        (legacy_cwd / "workspaces" / "instances").mkdir(parents=True)
        monkeypatch.chdir(legacy_cwd)

        legacy = resolve_legacy_cwd_state_dir()
        assert legacy is not None
        assert legacy == os.path.join(str(legacy_cwd), "workspaces")
        assert os.path.normcase(legacy) != os.path.normcase(resolve_registry_state_dir())

    def test_legacy_none_when_env_set(self, tmp_path, monkeypatch):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path / "env-root"))
        monkeypatch.chdir(tmp_path)
        assert resolve_legacy_cwd_state_dir() is None

    def test_project_root_contains_defaults(self):
        root = Path(resolve_project_root())
        assert (root / "config.defaults.json").is_file()

    def test_paths_equal_normcase(self):
        assert paths_equal(r"C:\Foo\workspaces", r"c:\foo\workspaces")
        assert not paths_equal(r"C:\Foo\workspaces", r"C:\Bar\workspaces")

    def test_registry_unaffected_by_paths_state_dir_env(
        self, tmp_path, monkeypatch, registry_root
    ):
        install_test_config(tmp_path, {"paths": {"state_dir": "workspaces"}})
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path / "env-root"))
        assert resolve_registry_state_dir() == str(registry_root)

    def test_registry_env_override(self, tmp_path, monkeypatch):
        custom = str(tmp_path / "custom-registry")
        monkeypatch.setenv("MINIAGENT_REGISTRY_STATE_DIR", custom)
        assert resolve_registry_state_dir() == custom

    def test_normalize_project_dir_matches_resolve_project_dir(
        self, tmp_path, monkeypatch
    ):
        project = tmp_path / "p"
        project.mkdir()
        monkeypatch.chdir(project)
        assert normalize_project_dir(str(project)) == resolve_project_dir()
