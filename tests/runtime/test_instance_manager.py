"""Tests for instance registry (multi-instance)."""

import os
import tempfile

import pytest

from miniagent.assistant.infrastructure.instance import InstanceRegistry, ProjectDirConflictError
from miniagent.assistant.infrastructure.paths import normalize_project_dir, resolve_project_key
from miniagent.assistant.state.registry import REGISTRY_DATABASE_NAME


def _fake_pid_checker(pid: int) -> bool:
    """测试用 PID 检查器，始终返回 True。"""
    return True


class TestInstanceRegistry:
    """测试多实例注册表。"""

    def test_register_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            instances = mgr.list_all()
            assert len(instances) >= 1
            assert any(i["pid"] == os.getpid() for i in instances)
            mgr.unregister()

    def test_unregister_removes_registry_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            assert mgr.list_all()
            mgr.unregister()
            assert mgr.list_all() == []
            assert os.path.isfile(os.path.join(tmpdir, REGISTRY_DATABASE_NAME))
            assert not os.path.exists(os.path.join(tmpdir, "instances"))

    def test_heartbeat_updates_typed_column(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            assert mgr._my_id is not None
            before = mgr._store.get(mgr._my_id)
            assert before is not None
            monkeypatch.setattr(mgr, "_now_ms", lambda: before.heartbeat_at_ms + 1)
            mgr.heartbeat()
            after = mgr._store.get(mgr._my_id)
            assert after is not None
            assert after.heartbeat_at_ms == before.heartbeat_at_ms + 1
            mgr.unregister()

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            assert mgr.list_all() == []

    def test_cleans_dead_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Register and immediately unregister (simulate dead)
            mgr1 = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr1.register(mode="cli")
            mgr1.unregister()

            # New instance should see clean list
            mgr2 = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr2.register(mode="cli")
            instances = mgr2.list_all()
            assert len(instances) == 1  # only itself
            mgr2.unregister()

    def test_old_instance_json_is_not_read_or_modified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            stale = Path(tmpdir) / "instances" / "1"
            stale.mkdir(parents=True)
            legacy = stale / "meta.json"
            legacy.write_text('{"pid": 99901}', encoding="utf-8")

            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            result = mgr.register(mode="cli")
            assert result["instance_id"] == 1
            assert legacy.read_text(encoding="utf-8") == '{"pid": 99901}'
            instances = mgr.list_all()
            assert len(instances) == 1
            assert instances[0]["pid"] == os.getpid()
            mgr.unregister()

    def test_register_preserves_alive_instance_dirs(self, monkeypatch, tmp_path):
        other_pid = 88802
        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()

        def checker(pid: int) -> bool:
            return pid in (other_pid, os.getpid())

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_a))
            first = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            first.register(mode="cli", active_sessions=["s"])
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            mgr.register(mode="cli")
            instances = mgr.list_all()
            assert len(instances) == 2
            ids = {i["instance_id"] for i in instances}
            assert ids == {1, 2}
            mgr.unregister()
            first.unregister()

    def test_format_table_empty(self):
        from miniagent.assistant.infrastructure.instance import format_instances_table

        result = format_instances_table([])
        assert "暂无" in result
        assert "注册表" in result

    def test_format_table_with_data(self):
        from miniagent.assistant.infrastructure.instance import format_instances_table

        fake = [
            {
                "instance_id": 1,
                "pid": 12345,
                "mode": "cli",
                "project_dir": "D:/my-app",
                "start_time": "2026-05-09T10:00:00",
                "active_sessions": ["default"],
                "hostname": "test",
            }
        ]
        result = format_instances_table(fake)
        assert "#1" in result
        assert "cli" in result
        assert "my-app" in result
        assert "both=CLI+飞书" in result

    def test_format_table_shows_workspace_column(self):
        from miniagent.assistant.infrastructure.instance import format_instances_table

        fake = [
            {
                "instance_id": 1,
                "pid": 12345,
                "mode": "cli",
                "project_dir": "D:/my-app",
                "project_key": "myapp-deadbeef",
                "start_time": "2026-05-09T10:00:00",
                "active_sessions": ["default"],
                "hostname": "test",
            }
        ]
        result = format_instances_table(fake)
        assert "Workspace" in result
        assert "projects/myapp-deadbeef" in result

    def test_format_project_conflict_message_includes_state_dir(
        self, monkeypatch, tmp_path
    ):
        from miniagent.assistant.infrastructure.instance import format_project_conflict_message

        reg = tmp_path / "registry"
        reg.mkdir()
        monkeypatch.setenv("MINIAGENT_REGISTRY_STATE_DIR", str(reg))
        project = tmp_path / "proj"
        project.mkdir()

        meta_with_dir = {
            "instance_id": 1,
            "pid": 999,
            "project_dir": str(project),
            "project_state_dir": str(tmp_path / "custom-ws"),
        }
        msg = format_project_conflict_message(meta_with_dir)
        assert "数据目录:" in msg
        assert str(tmp_path / "custom-ws") in msg

        stale_meta = {
            "instance_id": 2,
            "pid": 1000,
            "project_dir": str(project),
        }
        stale_msg = format_project_conflict_message(stale_meta)
        assert "数据目录:" in stale_msg
        assert "projects" in stale_msg

    def test_register_writes_project_meta(self, monkeypatch, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        ws = tmp_path / "ws"
        monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project))
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(ws))

        with tempfile.TemporaryDirectory() as regdir:
            mgr = InstanceRegistry(state_dir=regdir, pid_checker=_fake_pid_checker)
            meta = mgr.register(mode="cli")
            assert meta["project_dir"] == normalize_project_dir(str(project))
            assert meta["project_state_dir"] == str(ws)
            assert meta["project_key"] == resolve_project_key(str(project))
            mgr.unregister()

    def test_register_project_dir_conflict(self, monkeypatch, tmp_path):
        project = tmp_path / "same"
        project.mkdir()
        other_pid = 88099

        def checker(pid: int) -> bool:
            return pid in (other_pid, os.getpid())

        with tempfile.TemporaryDirectory() as regdir:
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project))
            first = InstanceRegistry(state_dir=regdir, pid_checker=checker)
            first.register(mode="cli")
            mgr = InstanceRegistry(state_dir=regdir, pid_checker=checker)
            with pytest.raises(ProjectDirConflictError):
                mgr.register(mode="cli")
            first.unregister()

    def test_register_different_project_dirs_allowed(self, monkeypatch, tmp_path):
        project_a = tmp_path / "a"
        project_b = tmp_path / "b"
        project_a.mkdir()
        project_b.mkdir()
        other_pid = 88100

        def checker(pid: int) -> bool:
            return pid in (other_pid, os.getpid())

        with tempfile.TemporaryDirectory() as regdir:
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_a))
            first = InstanceRegistry(state_dir=regdir, pid_checker=checker)
            first.register(mode="cli")
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr = InstanceRegistry(state_dir=regdir, pid_checker=checker)
            result = mgr.register(mode="cli")
            assert result["instance_id"] == 2
            assert len(mgr.list_all()) == 2
            mgr.unregister()
            first.unregister()

    def test_register_rejects_invalid_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            with pytest.raises(ValueError, match="instance mode"):
                mgr.register(mode="feishu")

    def test_update_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            mgr.update_mode("both")
            assert mgr._meta["mode"] == "both"
            assert mgr._my_id is not None
            stored = mgr._store.get(mgr._my_id)
            assert stored is not None
            assert stored.mode == "both"
            mgr.unregister()

    def test_update_mode_noop_without_register(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.update_mode("both")  # should not raise

    def test_update_mode_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            with pytest.raises(ValueError, match="instance mode"):
                mgr.update_mode("feishu")
            mgr.unregister()

    def test_update_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli", active_sessions=[])
            mgr.update_sessions(["session-a", "session-b"])
            assert mgr._meta["active_sessions"] == ["session-a", "session-b"]
            mgr.unregister()

    def test_stop_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            result = mgr.stop(999)
            assert result["success"] is False

    def test_stop_current_instance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            result = mgr.stop_current()
            assert result["success"] is True
            # Should have unregistered
            assert mgr._my_id is None

    def test_register_does_not_overwrite_alive_meta(self, monkeypatch, tmp_path):
        other_pid = 77703
        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()

        def checker(pid: int) -> bool:
            return pid in (other_pid, os.getpid())

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_a))
            first = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            first.register(mode="both")
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            mgr.register(mode="cli")
            assert mgr._my_id == 2
            stored = first._store.get(1)
            assert stored is not None
            assert stored.mode == "both"
            mgr.unregister()
            first.unregister()

    def test_register_same_project_dir_raises_conflict(self, monkeypatch, tmp_path):
        project = tmp_path / "same-project"
        project.mkdir()
        monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project))

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr1 = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr1.register(mode="cli")
            mgr2 = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            with pytest.raises(ProjectDirConflictError):
                mgr2.register(mode="cli")
            mgr1.unregister()

    def test_sequential_register_allocates_incrementing_ids(self, monkeypatch, tmp_path):
        """不同 project_dir 顺序注册应分配递增 ID。"""
        project_a = tmp_path / "a"
        project_b = tmp_path / "b"
        project_a.mkdir()
        project_b.mkdir()

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_a))
            mgr1 = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            r1 = mgr1.register(mode="cli")

            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr2 = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            r2 = mgr2.register(mode="cli")

            assert {r1["instance_id"], r2["instance_id"]} == {1, 2}
            mgr1.unregister()
            mgr2.unregister()

    def test_list_instances_cached_separate_keys(self, monkeypatch):
        from miniagent.assistant.infrastructure.instance import (
            list_instances_cached,
            reset_instance_registry_for_tests,
        )

        reset_instance_registry_for_tests()
        calls: list[str | None] = []

        def fake_list(state_dir=None):
            calls.append(state_dir)
            return []

        monkeypatch.setattr(
            "miniagent.assistant.infrastructure.instance.list_instances",
            fake_list,
        )

        list_instances_cached()
        list_instances_cached()
        list_instances_cached("other")
        assert len(calls) == 2
        assert calls == [None, "other"]

        list_instances_cached()
        assert len(calls) == 2
