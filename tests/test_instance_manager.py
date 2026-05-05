"""Tests for instance manager."""

import os
import tempfile
import pytest
from src.core.instance_manager import InstanceManager


class TestInstanceManager:
    """测试单实例管理器。"""

    def test_acquire(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceManager(state_dir=tmpdir)
            result = mgr.try_acquire()
            assert result["success"] is True
            assert os.path.exists(mgr._pid_file)

    def test_duplicate_acquire_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceManager(state_dir=tmpdir)
            mgr.try_acquire()
            result = mgr.try_acquire()
            assert result["success"] is False
            assert "existing_pid" in result

    def test_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceManager(state_dir=tmpdir)
            mgr.try_acquire()
            mgr.release()
            assert not os.path.exists(mgr._pid_file)

    def test_acquire_after_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr1 = InstanceManager(state_dir=tmpdir)
            mgr1.try_acquire()
            mgr1.release()

            mgr2 = InstanceManager(state_dir=tmpdir)
            result = mgr2.try_acquire()
            assert result["success"] is True

    def test_stop_when_no_instance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceManager(state_dir=tmpdir)
            result = mgr.stop()
            assert result["success"] is False
            assert "reason" in result

    @pytest.mark.skip(reason="force_acquire kills the test process itself")
    def test_force_acquire(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceManager(state_dir=tmpdir)
            mgr.try_acquire()
            result = mgr.force_acquire()
            assert "success" in result
