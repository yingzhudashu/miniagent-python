"""InstanceRegistry 同步/异步停止、死亡清理和错误映射测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from miniagent.infrastructure import instance as instance_module
from miniagent.infrastructure.instance import InstanceRegistry


def _write_instance(tmp_path, instance_id=1, pid=123):
    directory = tmp_path / "instances" / str(instance_id)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(
        json.dumps({"schema_version": 1, "instance_id": instance_id, "pid": pid}),
        encoding="utf-8",
    )
    return directory


def test_stop_dead_live_and_termination_error(tmp_path, monkeypatch) -> None:
    registry = InstanceRegistry(state_dir=str(tmp_path), pid_checker=lambda _pid: False)
    directory = _write_instance(tmp_path)
    dead = registry.stop(1)
    assert dead["success"] and "已不存在" in dead["reason"] and not directory.exists()

    directory = _write_instance(tmp_path)
    monkeypatch.setattr(instance_module, "is_process_running", lambda _pid: True)
    monkeypatch.setattr(instance_module.subprocess, "check_output", lambda *_args, **_kwargs: b"")
    assert registry.stop(1) == {"success": True}
    assert not directory.exists()

    _write_instance(tmp_path)
    monkeypatch.setattr(
        instance_module.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    failed = registry.stop(1)
    assert not failed["success"] and "denied" in failed["reason"]


@pytest.mark.asyncio
async def test_stop_async_dead_live_and_error(tmp_path, monkeypatch) -> None:
    registry = InstanceRegistry(state_dir=str(tmp_path), pid_checker=lambda _pid: False)
    directory = _write_instance(tmp_path)

    async def dead(_pid):
        return False

    monkeypatch.setattr(instance_module, "is_process_running_async", dead)
    result = await registry.stop_async(1)
    assert result["success"] and not directory.exists()

    directory = _write_instance(tmp_path)

    async def alive(_pid):
        return True

    async def create_proc(*_args, **_kwargs):
        return SimpleNamespace(wait=lambda: _completed())

    async def _completed():
        return 0

    monkeypatch.setattr(instance_module, "is_process_running_async", alive)
    monkeypatch.setattr(instance_module.asyncio, "create_subprocess_exec", create_proc)
    assert await registry.stop_async(1) == {"success": True}
    assert not directory.exists()

    _write_instance(tmp_path)

    async def fail_proc(*_args, **_kwargs):
        raise OSError("denied")

    monkeypatch.setattr(instance_module.asyncio, "create_subprocess_exec", fail_proc)
    failed = await registry.stop_async(1)
    assert not failed["success"] and "denied" in failed["reason"]


def test_stop_invalid_metadata_and_current_state(tmp_path) -> None:
    registry = InstanceRegistry(state_dir=str(tmp_path), pid_checker=lambda _pid: False)
    directory = tmp_path / "instances" / "1"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("bad", encoding="utf-8")
    assert "读取元数据失败" in registry.stop(1)["reason"]
    assert not registry.stop_current()["success"]

