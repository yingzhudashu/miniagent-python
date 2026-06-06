"""Tests for instance registry (multi-instance)."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from miniagent.infrastructure.instance import InstanceRegistry, ProjectDirConflictError
from miniagent.infrastructure.paths import normalize_project_dir, resolve_project_key


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

    def test_unregister_removes_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            my_dir = mgr._my_dir
            assert my_dir.exists()
            mgr.unregister()
            assert not my_dir.exists()

    def test_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=_fake_pid_checker)
            mgr.register(mode="cli")
            mgr.heartbeat()
            heartbeat_file = mgr._my_dir / "heartbeat"
            assert heartbeat_file.exists()
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

    def test_register_cleans_dead_instance_dirs(self):
        dead_pid = 99901

        def checker(pid: int) -> bool:
            return pid != dead_pid

        with tempfile.TemporaryDirectory() as tmpdir:
            stale = Path(tmpdir) / "instances" / "1"
            stale.mkdir(parents=True)
            meta = {
                "pid": dead_pid,
                "instance_id": 1,
                "mode": "cli",
                "active_sessions": [],
                "hostname": "test-host",
                "start_time": "2026-05-09T10:00:00",
            }
            (stale / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            mgr.register(mode="cli")
            # 清理后重用 ID 1，目录仍存在但 meta 已是当前进程
            assert stale.exists()
            with open(stale / "meta.json", encoding="utf-8") as f:
                disk = json.load(f)
            assert disk["pid"] == os.getpid()
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
            other = Path(tmpdir) / "instances" / "1"
            other.mkdir(parents=True)
            meta = {
                "pid": other_pid,
                "instance_id": 1,
                "mode": "cli",
                "active_sessions": ["s"],
                "hostname": "other",
                "start_time": "2026-05-09T11:00:00",
                "project_dir": str(project_a),
            }
            (other / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            mgr.register(mode="cli")
            assert other.exists()
            instances = mgr.list_all()
            assert len(instances) == 2
            ids = {i["instance_id"] for i in instances}
            assert ids == {1, 2}
            mgr.unregister()

    def test_format_table_empty(self):
        from miniagent.infrastructure.instance import format_instances_table

        result = format_instances_table([])
        assert "暂无" in result
        assert "注册表" in result

    def test_format_table_with_data(self):
        from miniagent.infrastructure.instance import format_instances_table

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
        from miniagent.infrastructure.instance import format_instances_table

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
        from miniagent.infrastructure.instance import format_project_conflict_message

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
            alive = Path(regdir) / "instances" / "1"
            alive.mkdir(parents=True)
            meta = {
                "pid": other_pid,
                "instance_id": 1,
                "mode": "cli",
                "active_sessions": [],
                "hostname": "other",
                "start_time": "2026-05-09T12:00:00",
                "project_dir": str(project),
            }
            (alive / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project))
            mgr = InstanceRegistry(state_dir=regdir, pid_checker=checker)
            with pytest.raises(ProjectDirConflictError):
                mgr.register(mode="cli")

    def test_register_different_project_dirs_allowed(self, monkeypatch, tmp_path):
        project_a = tmp_path / "a"
        project_b = tmp_path / "b"
        project_a.mkdir()
        project_b.mkdir()
        other_pid = 88100

        def checker(pid: int) -> bool:
            return pid in (other_pid, os.getpid())

        with tempfile.TemporaryDirectory() as regdir:
            other = Path(regdir) / "instances" / "1"
            other.mkdir(parents=True)
            meta = {
                "pid": other_pid,
                "instance_id": 1,
                "mode": "cli",
                "active_sessions": [],
                "hostname": "other",
                "start_time": "2026-05-09T12:00:00",
                "project_dir": str(project_a),
            }
            (other / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr = InstanceRegistry(state_dir=regdir, pid_checker=checker)
            result = mgr.register(mode="cli")
            assert result["instance_id"] == 2
            assert len(mgr.list_all()) == 2
            mgr.unregister()

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
            meta_path = mgr._my_dir / "meta.json"
            import json

            with open(meta_path, encoding="utf-8") as f:
                disk = json.load(f)
            assert disk["mode"] == "both"
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
            alive = Path(tmpdir) / "instances" / "1"
            alive.mkdir(parents=True)
            meta = {
                "pid": other_pid,
                "instance_id": 1,
                "mode": "both",
                "active_sessions": [],
                "hostname": "alive",
                "start_time": "2026-05-09T12:00:00",
                "project_dir": str(project_a),
            }
            (alive / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

            monkeypatch.setenv("MINIAGENT_PROJECT_DIR", str(project_b))
            mgr = InstanceRegistry(state_dir=tmpdir, pid_checker=checker)
            mgr.register(mode="cli")
            assert mgr._my_id == 2
            with open(alive / "meta.json", encoding="utf-8") as f:
                disk = json.load(f)
            assert disk["pid"] == other_pid
            mgr.unregister()

    def test_list_instances_merges_multiple_roots(self, monkeypatch):
        from miniagent.infrastructure.instance import (
            list_instances,
            reset_instance_registry_for_tests,
        )

        with tempfile.TemporaryDirectory() as canonical, tempfile.TemporaryDirectory() as legacy:
            canon_pid = 91001
            legacy_pid = 91002

            def fake_running(pid: int) -> bool:
                return pid in (canon_pid, legacy_pid)

            monkeypatch.setattr(
                "miniagent.infrastructure.instance.is_process_running",
                fake_running,
            )

            def _seed(root: str, iid: int, pid: int, mode: str) -> None:
                d = Path(root) / "instances" / str(iid)
                d.mkdir(parents=True)
                (d / "meta.json").write_text(
                    json.dumps(
                        {
                            "pid": pid,
                            "instance_id": iid,
                            "mode": mode,
                            "active_sessions": [],
                            "hostname": "h",
                            "start_time": "2026-05-09T10:00:00",
                        }
                    ),
                    encoding="utf-8",
                )

            _seed(canonical, 1, canon_pid, "cli")
            _seed(legacy, 1, legacy_pid, "both")

            monkeypatch.setattr(
                "miniagent.infrastructure.instance._instance_registry_roots",
                lambda **_: [canonical, legacy],
            )
            reset_instance_registry_for_tests()

            found = list_instances()
            assert len(found) == 2
            pids = {i["pid"] for i in found}
            assert pids == {canon_pid, legacy_pid}
            dirs = {i["state_dir"] for i in found}
            assert dirs == {canonical, legacy}

    def test_stop_instance_by_id_requires_state_dir_when_ambiguous(self, monkeypatch):
        from miniagent.infrastructure.instance import (
            list_instances,
            reset_instance_registry_for_tests,
            stop_instance_by_id,
        )

        pid_a, pid_b = 92001, 92002

        def fake_running(pid: int) -> bool:
            return pid in (pid_a, pid_b)

        monkeypatch.setattr(
            "miniagent.infrastructure.instance.is_process_running",
            fake_running,
        )

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for root, pid in ((a, pid_a), (b, pid_b)):
                d = Path(root) / "instances" / "1"
                d.mkdir(parents=True)
                (d / "meta.json").write_text(
                    json.dumps(
                        {
                            "pid": pid,
                            "instance_id": 1,
                            "mode": "cli",
                            "active_sessions": [],
                            "hostname": "h",
                            "start_time": "2026-05-09T10:00:00",
                        }
                    ),
                    encoding="utf-8",
                )

            reset_instance_registry_for_tests()

            import miniagent.infrastructure.instance as inst_mod

            orig = inst_mod._instance_registry_roots
            try:
                inst_mod._instance_registry_roots = lambda **_: [a, b]
                assert len(list_instances()) == 2
                r = stop_instance_by_id(1)
                assert r["success"] is False
                assert "多个状态目录" in r["reason"]
            finally:
                inst_mod._instance_registry_roots = orig
                reset_instance_registry_for_tests()

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
        from miniagent.infrastructure.instance import (
            list_instances_cached,
            reset_instance_registry_for_tests,
        )

        reset_instance_registry_for_tests()
        calls: list[tuple] = []

        def fake_list(state_dir=None, *, include_legacy_cwd=True):
            calls.append((state_dir, include_legacy_cwd))
            return []

        monkeypatch.setattr(
            "miniagent.infrastructure.instance.list_instances",
            fake_list,
        )

        list_instances_cached()
        list_instances_cached(include_legacy_cwd=False)
        assert len(calls) == 2
        assert calls[0] == (None, True)
        assert calls[1] == (None, False)

        list_instances_cached()
        assert len(calls) == 2
