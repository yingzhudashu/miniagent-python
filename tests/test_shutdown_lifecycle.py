"""关停与资源：shutdown_runtime、登记 task、子进程追踪、飞书 stop_async。"""

from __future__ import annotations

import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.infrastructure.process import cleanup_all_processes
from miniagent.runtime.context import RuntimeContext
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.scheduled_tasks.store import save_tasks
from miniagent.scheduled_tasks.ticker import tick_once
from tests.scheduled_tasks_helpers import patch_tick_once_locks


def _minimal_ctx() -> RuntimeContext:
    mq = MessageQueueManager()
    router = MagicMock()
    router.primary = "default"
    return RuntimeContext(
        registry=MagicMock(),
        monitor=MagicMock(),
        skill_registry=MagicMock(),
        clawhub=MagicMock(),
        engine=MagicMock(),
        channel_router=router,
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=MagicMock(),
        activity_log=MagicMock(),
        keyword_index=MagicMock(),
        memory_context=MagicMock(),
        openai_client=None,
    )


@pytest.mark.asyncio
async def test_shutdown_runtime_cancels_tracked_tasks() -> None:
    ctx = _minimal_ctx()
    state: dict = {"active_session_id": ""}

    async def _slow() -> None:
        await asyncio.sleep(3600)

    t = asyncio.create_task(_slow())
    ctx.register_shutdown_tracked_task(t)
    await shutdown_runtime(
        ctx,
        state,  # type: ignore[arg-type]
        reason="test_tracked",
        abort_message_queues=False,
        release_cli_session_lock=False,
        call_unregister=False,
    )
    assert t.done()
    assert ctx.shutdown_tracked_tasks == set() or all(x.done() for x in ctx.shutdown_tracked_tasks)


@pytest.mark.asyncio
async def test_shutdown_runtime_feishu_stop_async_noop_when_never_started() -> None:
    ctx = _minimal_ctx()
    state: dict = {"active_session_id": ""}
    await shutdown_runtime(
        ctx,
        state,  # type: ignore[arg-type]
        reason="test_no_feishu",
        abort_message_queues=False,
        release_cli_session_lock=False,
        call_unregister=False,
    )
    assert ctx.feishu.get_task() is None


@pytest.mark.asyncio
async def test_feishu_stop_async_awaits_cancelled_poll_task() -> None:
    """start → 可取消的 poll stub → stop_async 应结束 task（不依赖 reset 打桩次数；闭包会绑定首次 import 的符号）。"""
    mq = MessageQueueManager()
    fe = FeishuRuntime(mq)

    async def fake_poll(*_a, **_k):
        await asyncio.Event().wait()

    with (
        patch.dict(
            "os.environ",
            {"FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y", "FEISHU_VERIFICATION_TOKEN": "z"},
        ),
        patch(
            "miniagent.infrastructure.feishu_inbound_lock.try_acquire_feishu_inbound_owner",
            return_value=(True, "ok"),
        ),
        patch(
            "miniagent.feishu.poll_server.start_feishu_poll_server",
            new=fake_poll,
        ),
        patch(
            "miniagent.feishu.poll_server.reset_feishu_ws_singleton",
            new_callable=AsyncMock,
        ),
    ):
        fe.start(
            lambda *_a, **_k: (AsyncMock(), None),
            {"instance_id": 1},
        )
        task = fe.get_task()
        assert task is not None
        await fe.stop_async()
        assert task.done()
        assert fe.get_task() is None


@pytest.mark.asyncio
async def test_cleanup_all_processes_clears_after_child_exits() -> None:
    """已结束子进程经 cleanup 后追踪表清空（不依赖跨平台强杀语义）。"""
    from miniagent.infrastructure.process import get_tracked_count, register_process

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "pass",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await register_process(proc)
    await proc.wait()
    assert proc.returncode == 0
    await cleanup_all_processes()
    assert get_tracked_count() == 0


@pytest.mark.asyncio
async def test_shutdown_runtime_named_probe_task_not_still_running() -> None:
    """关停后带显式名称的登记任务应已结束（比全量 all_tasks 白名单更稳，避免 pytest 噪声）。"""
    ctx = _minimal_ctx()
    state: dict = {"active_session_id": ""}

    async def _slow() -> None:
        await asyncio.sleep(3600)

    t = asyncio.create_task(_slow())
    t.set_name("miniagent_shutdown_probe_slow")
    ctx.register_shutdown_tracked_task(t)
    await shutdown_runtime(
        ctx,
        state,  # type: ignore[arg-type]
        reason="test_named_probe",
        abort_message_queues=False,
        release_cli_session_lock=False,
        call_unregister=False,
    )
    for x in asyncio.all_tasks():
        if x.get_name() == "miniagent_shutdown_probe_slow":
            assert x.done(), "registered slow task should be cancelled"


@pytest.mark.asyncio
async def test_tick_once_job_registered_then_shutdown_cancels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """tick_once 派生的 _one_job 登记到 ctx 后，shutdown_runtime 可将其取消。"""
    from miniagent.scheduled_tasks import ticker as ticker_mod

    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIAGENT_DISABLE_SCHEDULED_TASKS", "0")
    ticker_mod._inflight.clear()

    t = ScheduledTask(
        id="j_shutdown",
        name="j_shutdown",
        prompt="p",
        enabled=True,
        schedule=ScheduleSpec(kind="interval", interval_seconds=3600),
        session=SessionSpec(mode="primary"),
        next_run_at=time.time() - 1.0,
    )
    due_at = float(t.next_run_at or 0)
    save_tasks([t])

    async def _slow_coro() -> None:
        await asyncio.sleep(120)

    def _fake_build(*_a: object, **_k: object) -> tuple[object, str]:
        return (_slow_coro(), "__cli__")

    monkeypatch.setattr(
        "miniagent.scheduled_tasks.ticker.build_run_scheduled_job_coro",
        _fake_build,
    )
    patch_tick_once_locks(monkeypatch)

    ctx = _minimal_ctx()
    st: CliLoopState = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }

    await tick_once(ctx, st, [], [])
    pending = [x for x in ctx.shutdown_tracked_tasks if not x.done()]
    assert pending, "expected tick_once to register a running job task"

    await shutdown_runtime(
        ctx,
        st,
        reason="test_tick_shutdown",
        abort_message_queues=True,
        release_cli_session_lock=False,
        call_unregister=False,
    )
    assert all(x.done() for x in pending)

    from miniagent.scheduled_tasks.store import load_tasks

    loaded = load_tasks()
    assert len(loaded) == 1
    nxt = loaded[0].next_run_at
    assert nxt is not None
    assert nxt <= due_at + 1.0


@pytest.mark.asyncio
async def test_shutdown_runtime_aborts_message_queues() -> None:
    ctx = _minimal_ctx()
    state: dict = {"active_session_id": ""}
    ctx.message_queue.abort_all_chats = MagicMock(return_value={"chats": {}})

    await shutdown_runtime(
        ctx,
        state,  # type: ignore[arg-type]
        reason="test_abort_mq",
        abort_message_queues=True,
        release_cli_session_lock=False,
        call_unregister=False,
    )
    ctx.message_queue.abort_all_chats.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_runtime_continues_after_cleanup_processes_failure() -> None:
    ctx = _minimal_ctx()
    state: dict = {"active_session_id": ""}
    trace_called: list[str] = []

    async def _boom() -> None:
        raise RuntimeError("cleanup failed")

    with (
        patch("miniagent.engine.shutdown.cleanup_all_processes", new=_boom),
        patch(
            "miniagent.infrastructure.tracing.shutdown_trace_writer",
            side_effect=lambda: trace_called.append("trace"),
        ),
    ):
        await shutdown_runtime(
            ctx,
            state,  # type: ignore[arg-type]
            reason="test_cleanup_fail",
            abort_message_queues=False,
            release_cli_session_lock=False,
            call_unregister=False,
        )

    assert trace_called == ["trace"]


@pytest.mark.asyncio
async def test_shutdown_runtime_invokes_resource_teardown() -> None:
    ctx = _minimal_ctx()
    state: dict = {"active_session_id": ""}

    drive_mock = AsyncMock()
    embed_mock = AsyncMock()
    clawhub_mock = AsyncMock()
    config_mock = MagicMock()
    trace_mock = MagicMock()

    with (
        patch("miniagent.feishu.drive_client.close_http_client", drive_mock),
        patch("miniagent.memory.embedding_search.close_embed_http_client", embed_mock),
        patch("miniagent.skills.clawhub_client.close_clawhub_client", clawhub_mock),
        patch("miniagent.infrastructure.config_watch.stop_config_watch", config_mock),
        patch("miniagent.infrastructure.tracing.shutdown_trace_writer", trace_mock),
    ):
        await shutdown_runtime(
            ctx,
            state,  # type: ignore[arg-type]
            reason="test_resource_teardown",
            abort_message_queues=False,
            release_cli_session_lock=False,
            call_unregister=False,
        )

    drive_mock.assert_awaited_once()
    embed_mock.assert_awaited_once()
    clawhub_mock.assert_awaited_once()
    config_mock.assert_called_once_with(ctx)
    trace_mock.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="tracked child kill path flaky on Windows CI")
async def test_cleanup_all_processes_kills_long_running_tracked_child() -> None:
    from miniagent.infrastructure.process import create_tracked_subprocess, get_tracked_count

    proc = await create_tracked_subprocess(
        f'"{sys.executable}" -c "import time; time.sleep(120)"',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert get_tracked_count() >= 1
    assert proc.returncode is None
    await cleanup_all_processes()
    await asyncio.wait_for(proc.wait(), timeout=25.0)
    assert proc.returncode is not None
