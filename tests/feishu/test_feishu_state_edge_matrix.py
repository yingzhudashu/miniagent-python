"""飞书运行时任务所有权、停止幂等与状态展示矩阵。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.engine.feishu_state import FeishuRuntime


class _FakeTask:
    def __init__(self, *, done: bool = False, cancelled: bool = False, error=None) -> None:
        self._done = done
        self._cancelled = cancelled
        self._error = error
        self.callbacks = []
        self.cancelled_by_runtime = False

    def done(self) -> bool:
        return self._done

    def cancelled(self) -> bool:
        return self._cancelled

    def exception(self):
        return self._error

    def cancel(self) -> None:
        self.cancelled_by_runtime = True

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)


def test_runtime_instance_id_done_callback_and_accessors() -> None:
    runtime = FeishuRuntime(MagicMock())
    assert runtime._instance_id(None) is None
    assert runtime._instance_id({"instance_id": "4"}) == 4
    assert runtime._instance_id({"instance_id": "bad"}) is None

    completed = _FakeTask(done=True, error=RuntimeError("boom"))
    runtime.set_task(completed)  # type: ignore[arg-type]
    runtime.set_running(True)
    runtime._on_runtime_task_done(completed)  # type: ignore[arg-type]
    assert runtime.get_task() is None and not runtime.is_running()

    cancelled = _FakeTask(done=True, cancelled=True)
    runtime.set_task(cancelled)  # type: ignore[arg-type]
    runtime._on_runtime_task_done(cancelled)  # type: ignore[arg-type]
    runtime.set_config("cfg")
    assert runtime.get_config() == "cfg"


def test_runtime_stop_cancels_owned_task(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    runtime = FeishuRuntime(MagicMock())
    runtime._user_status = lines.append
    task = _FakeTask()
    runtime.set_task(task)  # type: ignore[arg-type]
    runtime.set_running(True)
    runtime._poll_state = SimpleNamespace(request_shutdown=MagicMock(side_effect=RuntimeError))
    monkeypatch.setattr("miniagent.assistant.infrastructure.instance.update_instance_mode", MagicMock())

    runtime.stop()
    assert task.cancelled_by_runtime
    assert task.callbacks
    task.callbacks[0](task)
    assert runtime.get_task() is None
    assert any("已停止" in line for line in lines)


def test_runtime_stop_when_idle_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    runtime = FeishuRuntime(MagicMock())
    runtime._user_status = lines.append
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.release_feishu_inbound_owner",
        MagicMock(),
    )
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.read_feishu_inbound_owner",
        lambda: {"alive": True, "pid": 1, "instance_id": 2},
    )
    runtime.stop()
    runtime._poll_state = SimpleNamespace(
        ws_health=SimpleNamespace(
            last_session_end=lambda: ("closed", None), last_inbound_monotonic=None
        )
    )
    runtime.status()
    assert any("未运行" in line for line in lines)
    assert any("closed" in line for line in lines)
    assert any("PID=1" in line for line in lines)


@pytest.mark.asyncio
async def test_runtime_stop_async_idle_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    released = MagicMock()
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.feishu_inbound_lock.release_feishu_inbound_owner", released
    )
    runtime = FeishuRuntime(MagicMock())
    await runtime.stop_async()
    released.assert_called_once()
