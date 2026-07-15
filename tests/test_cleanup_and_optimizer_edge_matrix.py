"""后台清理与自优化的容错、回滚和安全边界。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import bg_session_cleanup
from miniagent.assistant.self_opt import auto_optimizer
from miniagent.assistant.self_opt.types import FileChange, OptimizationProposal, OptTestCase


def _proposal(*, files=(), tests=(), risk: str = "low") -> OptimizationProposal:
    return OptimizationProposal(
        id="proposal-1",
        type="optimize",
        risk_level=risk,
        target="test",
        description="test",
        rationale="test",
        expected_benefit="test",
        estimated_effort=1,
        files=list(files),
        test_cases=list(tests),
    )


@pytest.mark.asyncio
async def test_cleanup_collaborator_failures_are_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class BrokenManager:
        def forget_session(self, _key: str) -> None:
            raise RuntimeError("manager")

    broken = MagicMock(side_effect=RuntimeError("memory"))
    memory = SimpleNamespace(
        state_root=str(tmp_path),
        store=SimpleNamespace(evict_session=broken),
        remove_session_entries=broken,
        activity_log=SimpleNamespace(remove_session=AsyncMock(side_effect=RuntimeError("log"))),
    )
    monkeypatch.setattr(bg_session_cleanup, "_release_background_session_lock", AsyncMock())
    monkeypatch.setattr(bg_session_cleanup, "_remove_background_agent_memory", AsyncMock())
    monkeypatch.setattr(bg_session_cleanup, "_remove_background_traces", AsyncMock())

    await bg_session_cleanup.cleanup_background_session_artifacts(
        "__bg__broken", session_manager=BrokenManager(), memory=memory
    )

    assert broken.call_count == 2
    memory.activity_log.remove_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_helpers_swallow_optional_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bg_session_cleanup,
        "_remove_session_trace_events",
        AsyncMock(side_effect=RuntimeError("trace")),
    )
    await bg_session_cleanup._remove_background_traces("__bg__x")

    memory = SimpleNamespace(
        store=SimpleNamespace(evict_session=None),
        activity_log=SimpleNamespace(remove_session=None),
        remove_session_entries=MagicMock(),
    )
    await bg_session_cleanup._remove_background_memory_entries("__bg__x", memory)
    await bg_session_cleanup._remove_background_activity_log("__bg__x", memory)
    await bg_session_cleanup._forget_background_session("__bg__x", None)


def test_optimizer_file_changes_and_backups(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    created = FileChange(action="create", path="nested/new.txt", content="new")
    assert auto_optimizer._apply_file_change_sync(created, str(tmp_path))
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "new"

    renamed = FileChange(action="rename", old_path="nested/new.txt", path="moved.txt", content="")
    backups = auto_optimizer._collect_file_backups(_proposal(files=[renamed]), str(tmp_path))
    assert auto_optimizer._apply_file_change_sync(renamed, str(tmp_path))
    assert (tmp_path / "moved.txt").exists()
    auto_optimizer._restore_file_backups(backups)
    assert (tmp_path / "nested" / "new.txt").exists()
    assert not (tmp_path / "moved.txt").exists()

    monkeypatch.setattr(auto_optimizer, "open", MagicMock(side_effect=OSError("denied")), raising=False)
    assert not auto_optimizer._apply_file_change_sync(created, str(tmp_path))


@pytest.mark.asyncio
async def test_optimizer_skip_dry_run_and_failed_change(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = await auto_optimizer.apply_proposal(_proposal())
    high = await auto_optimizer.apply_proposal(
        _proposal(files=[FileChange(action="create", path="a", content="x")], risk="high")
    )
    monkeypatch.setattr(auto_optimizer, "is_in_git_repo_async", AsyncMock(return_value=False))
    dry = await auto_optimizer.apply_proposal(
        _proposal(files=[FileChange(action="create", path="a", content="x")]), dry_run=True
    )
    monkeypatch.setattr(auto_optimizer, "_apply_file_change", AsyncMock(return_value=False))
    failed = await auto_optimizer.apply_proposal(
        _proposal(files=[FileChange(action="create", path="a", content="x")]),
        auto_rollback=False,
    )

    assert empty.status == "skipped" and "无可执行" in empty.error
    assert high.status == "skipped" and "高风险" in high.error
    assert dry.status == "success" and dry.changes_applied == 1
    assert failed.status == "failed" and "变更失败" in failed.error


@pytest.mark.asyncio
async def test_optimizer_snapshot_and_file_rollback(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    change = FileChange(action="update", path="a.txt", content="new")
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(auto_optimizer, "is_in_git_repo_async", AsyncMock(return_value=True))
    monkeypatch.setattr(auto_optimizer, "has_uncommitted_changes_async", AsyncMock(return_value=True))
    monkeypatch.setattr(
        auto_optimizer,
        "create_snapshot_async",
        AsyncMock(return_value={"success": True, "ref": "stash@{0}"}),
    )
    monkeypatch.setattr(auto_optimizer, "_apply_file_change", AsyncMock(return_value=False))
    rollback = AsyncMock(return_value={"success": True, "message": "ok"})
    monkeypatch.setattr(auto_optimizer, "rollback_snapshot_async", rollback)

    result = await auto_optimizer.apply_proposal(_proposal(files=[change]), root=str(tmp_path))
    assert result.status == "failed" and "已回滚" in result.error
    rollback.assert_awaited_once()

    failure = await auto_optimizer._rollback_proposal(
        enabled=True,
        snapshot_ref="stash@{0}",
        root=str(tmp_path),
        file_backups={},
    )
    assert failure == " (已回滚)"


@pytest.mark.asyncio
async def test_optimizer_validation_failure_restores_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    path = tmp_path / "a.txt"
    path.write_text("old", encoding="utf-8")
    proposal = _proposal(
        files=[FileChange(action="update", path="a.txt", content="new")],
        tests=[OptTestCase(id="bad", command="command-does-not-exist")],
    )
    monkeypatch.setattr(auto_optimizer, "is_in_git_repo_async", AsyncMock(return_value=False))

    result = await auto_optimizer.apply_proposal(proposal, root=str(tmp_path))

    assert result.status == "failed" and "已回滚" in result.error
    assert path.read_text(encoding="utf-8") == "old"
