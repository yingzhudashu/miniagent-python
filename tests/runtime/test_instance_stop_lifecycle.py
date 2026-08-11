"""InstanceRegistry 同步/异步停止、死亡清理和错误映射测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from miniagent.assistant.infrastructure import instance as instance_module
from miniagent.assistant.infrastructure.instance import InstanceRegistry


def _register_target(registry: InstanceRegistry, pid: int = 123) -> int:
    instance = registry._store.register(
        project_dir=f"C:/target-{time.time_ns()}",
        project_key="target",
        project_state_dir="C:/state",
        pid=pid,
        mode="cli",
        active_sessions=(),
        hostname="test",
        now_ms=int(time.time() * 1000),
        alive_pid=lambda _pid: True,
        stale_before_ms=0,
    )
    return instance.instance_id


def test_stop_dead_live_and_termination_error(tmp_path, monkeypatch) -> None:
    registry = InstanceRegistry(state_dir=str(tmp_path), pid_checker=lambda _pid: False)
    target = _register_target(registry)
    dead = registry.stop(target)
    assert dead["success"] and "已不存在" in dead["reason"]
    assert registry._store.get(target) is None

    target = _register_target(registry)
    monkeypatch.setattr(instance_module, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(instance_module.subprocess, "check_output", lambda *_args, **_kwargs: b"")
    assert registry.stop(target) == {"success": True}
    assert registry._store.get(target) is None

    target = _register_target(registry)
    monkeypatch.setattr(
        instance_module.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    failed = registry.stop(target)
    assert not failed["success"] and "denied" in failed["reason"]
    assert registry._store.get(target) is not None


@pytest.mark.asyncio
async def test_stop_async_dead_live_and_error(tmp_path, monkeypatch) -> None:
    registry = InstanceRegistry(state_dir=str(tmp_path), pid_checker=lambda _pid: False)
    target = _register_target(registry)

    async def dead(_pid):
        return False

    monkeypatch.setattr(instance_module, "is_process_running_async", dead)
    result = await registry.stop_async(target)
    assert result["success"] and registry._store.get(target) is None

    target = _register_target(registry)

    async def alive(_pid):
        return True

    async def create_proc(*_args, **_kwargs):
        return SimpleNamespace(wait=lambda: _completed())

    async def _completed():
        return 0

    monkeypatch.setattr(instance_module, "is_process_running_async", alive)
    monkeypatch.setattr(instance_module.asyncio, "create_subprocess_exec", create_proc)
    assert await registry.stop_async(target) == {"success": True}
    assert registry._store.get(target) is None

    target = _register_target(registry)

    async def fail_proc(*_args, **_kwargs):
        raise OSError("denied")

    monkeypatch.setattr(instance_module.asyncio, "create_subprocess_exec", fail_proc)
    failed = await registry.stop_async(target)
    assert not failed["success"] and "denied" in failed["reason"]
    assert registry._store.get(target) is not None


def test_stop_missing_and_current_state(tmp_path) -> None:
    registry = InstanceRegistry(state_dir=str(tmp_path), pid_checker=lambda _pid: False)
    assert "不存在" in registry.stop(1)["reason"]
    assert not registry.stop_current()["success"]
