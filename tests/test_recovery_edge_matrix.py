"""恢复、降级、锁释放与受控失败路径矩阵。"""

from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.agent.observability import AsyncTraceWriter
from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.engine import bg_session_cleanup
from miniagent.assistant.scheduled_tasks import ticker
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec
from miniagent.assistant.self_opt import auto_optimizer
from miniagent.assistant.tools import filesystem
from miniagent.llm import legacy_transport as llm_transport


def test_responses_fallback_and_stream_event_edges() -> None:
    empty = llm_transport._response_fallback_events("")
    text = llm_transport._response_fallback_events("answer")
    assert len(empty) == 1 and empty[0].completed
    assert text[0].content_delta == "answer" and text[-1].completed

    response = {
        "output_text": "done",
        "output": [
            {"type": "function_call", "call_id": "call", "name": "tool", "arguments": "{}"}
        ],
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }
    events = llm_transport._response_fallback_events(response)
    assert any(event.tool_call_delta for event in events)
    assert events[-1].incomplete_reason == "max_output_tokens"

    state = llm_transport._ResponseEventState()
    assert llm_transport._normalize_response_stream_event(
        SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(type="message")),
        state,
    ) == []
    assert llm_transport._normalize_response_stream_event(
        SimpleNamespace(type="unknown"), state
    ) == []
    with pytest.raises(llm_transport.LLMTransportError):
        llm_transport._normalize_response_stream_event(
            SimpleNamespace(type="response.failed"), state
        )


@pytest.mark.asyncio
async def test_optimizer_rollback_failure_empty_and_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auto_optimizer,
        "rollback_snapshot_async",
        AsyncMock(return_value={"success": False, "message": "conflict"}),
    )
    result = await auto_optimizer._rollback_proposal(
        enabled=True, snapshot_ref="snapshot", root=".", file_backups={}
    )
    assert "Git 回滚失败" in result
    assert await auto_optimizer._rollback_proposal(
        enabled=True, snapshot_ref="", root=".", file_backups={}
    ) == ""
    proposal = auto_optimizer.OptimizationProposal(id="empty")
    alias = await auto_optimizer.run_auto_optimization(proposal)
    assert alias.status == "skipped"


@pytest.mark.asyncio
async def test_cleanup_optional_agent_trace_and_lock_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.engine.session_lock.release_session_lock", MagicMock(side_effect=RuntimeError)
    )
    await bg_session_cleanup._release_background_session_lock("__bg__x")

    monkeypatch.setattr(
        "miniagent.assistant.memory.layered_memory.remove_agent_longterm_entries_for_session",
        MagicMock(side_effect=RuntimeError),
    )
    await bg_session_cleanup._remove_background_agent_memory("__bg__x")
    monkeypatch.setattr(
        bg_session_cleanup, "_remove_session_trace_events", AsyncMock(return_value=2)
    )
    await bg_session_cleanup._remove_background_traces("__bg__x")


def test_scheduler_sleep_selection_and_trace_write_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ticker._sleep_seconds_until([]) == 60.0
    monkeypatch.setattr(ticker.time, "time", lambda: 100.0)
    active = ScheduledTask(
        id="active",
        name="active",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60),
        next_run_at=101.0,
    )
    disabled = ScheduledTask(id="disabled", name="d", prompt="p", enabled=False, next_run_at=1)
    future = ScheduledTask(id="future", name="f", prompt="p", next_run_at=200)
    assert ticker._sleep_seconds_until([active]) == 1.0
    monkeypatch.setattr(ticker, "try_acquire_job_lock", lambda _id: True)
    assert ticker._select_due_tasks([disabled, future, active], 150) == [active]

    writer = AsyncTraceWriter()
    writer._flush_trace_lines = MagicMock(side_effect=OSError("disk"))
    writer._write_trace_batch([("2026-01-01", "{}\n")])
    assert writer._write_error_count == 1 and writer._dropped_count == 1
    assert writer._collect_writer_batch() is None
    writer._queue = queue.Queue(maxsize=1)
    writer._queue.put_nowait({"type": "x"})
    writer._shutdown = True
    batch = writer._collect_writer_batch()
    assert batch is not None


@pytest.mark.asyncio
async def test_scheduler_finalize_failure_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    ticker._inflight.add("job")
    release = MagicMock()
    monkeypatch.setattr(ticker, "load_tasks", MagicMock(side_effect=RuntimeError("disk")))
    monkeypatch.setattr(ticker, "release_job_lock", release)
    await ticker._finalize_scheduled_job("job", outcome="completed", agent_error=None)
    assert "job" not in ticker._inflight
    release.assert_called_once_with("job")


@pytest.mark.asyncio
async def test_filesystem_move_copy_errors_and_recursive_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = ToolContext(cwd=str(tmp_path), allowed_paths=[str(tmp_path)])
    missing_move = await filesystem._move_file_handler(
        {"from": "missing", "to": "dest"}, ctx
    )
    missing_copy = await filesystem._copy_file_handler(
        {"from": "missing", "to": "dest"}, ctx
    )
    assert not missing_move.success and not missing_copy.success

    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    monkeypatch.setattr(filesystem.shutil, "move", MagicMock(side_effect=OSError("move")))
    move_error = await filesystem._move_file_handler(
        {"from": "source.txt", "to": "moved.txt"}, ctx
    )
    monkeypatch.setattr(filesystem.shutil, "copy2", MagicMock(side_effect=OSError("copy")))
    copy_error = await filesystem._copy_file_handler(
        {"from": "source.txt", "to": "copy.txt"}, ctx
    )
    assert "移动失败" in move_error.content and "复制失败" in copy_error.content

    child = tmp_path / "child"
    child.mkdir()
    (child / "nested.txt").write_text("x", encoding="utf-8")
    entries, _ = filesystem._walk_directory(
        tmp_path, detail=True, max_depth=2, max_entries=20
    )
    assert any("nested.txt" in entry for entry in entries)
