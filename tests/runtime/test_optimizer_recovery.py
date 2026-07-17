"""Focused regressions migrated from test_recovery_edge_matrix.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.self_opt import auto_optimizer


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
