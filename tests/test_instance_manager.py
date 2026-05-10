"""Tests for instance registry (multi-instance)."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from miniagent.infrastructure.instance import InstanceRegistry


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
            (stale / "meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )

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

    def test_register_preserves_alive_instance_dirs(self):
        other_pid = 88802

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
            }
            (other / "meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )

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

    def test_format_table_with_data(self):
        from miniagent.infrastructure.instance import format_instances_table
        fake = [{
            "instance_id": 1,
            "pid": 12345,
            "mode": "cli",
            "start_time": "2026-05-09T10:00:00",
            "active_sessions": ["default"],
            "hostname": "test",
        }]
        result = format_instances_table(fake)
        assert "#1" in result
        assert "cli" in result
        assert "both=CLI+飞书" in result

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
