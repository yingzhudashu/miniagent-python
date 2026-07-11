"""Tests for self_opt bug fixes and improved behavior."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from miniagent.core.self_opt.auto_optimizer import apply_proposal
from miniagent.core.self_opt.proposal_store import ProposalStore, get_history_file
from miniagent.core.self_opt.runtime_analyzer import _detect_loop_patterns
from miniagent.core.self_opt.types import FileChange, OptimizationProposal, OptTestCase
from miniagent.infrastructure.trace_events import EVENT_TOOL_END
from tests.config_helpers import install_test_config


@pytest.fixture
def proposal_output_dir(tmp_path: Path) -> Path:
    proposal_dir = tmp_path / "proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    return proposal_dir


class TestCleanupOldProposals:
    def test_cleanup_old_proposals_no_crash(self, tmp_path: Path, proposal_output_dir: Path) -> None:
        install_test_config(
            tmp_path,
            {"self_optimization": {"proposal_output_dir": str(proposal_output_dir)}},
        )
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        old_file = proposal_output_dir / f"proposals-{old_date}.jsonl"
        old_file.write_text('{"id":"old"}\n', encoding="utf-8")

        deleted = ProposalStore.cleanup_old_proposals(retention_days=30)
        assert deleted >= 1
        assert not old_file.exists()


class TestCrossDayProposalStatus:
    def test_update_status_finds_yesterday_proposal(
        self, tmp_path: Path, proposal_output_dir: Path
    ) -> None:
        install_test_config(
            tmp_path,
            {"self_optimization": {"proposal_output_dir": str(proposal_output_dir)}},
        )
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        proposal_file = proposal_output_dir / f"proposals-{yesterday}.jsonl"
        record = {
            "id": "opt-crossday",
            "status": "pending",
            "source": "runtime_analysis",
            "created_at": "2026-06-04T00:00:00Z",
            "updated_at": "2026-06-04T00:00:00Z",
            "proposal": {
                "id": "opt-crossday",
                "type": "optimize",
                "risk_level": "low",
                "target": "test",
                "description": "cross day",
                "rationale": "",
                "expected_benefit": "",
                "estimated_effort": 0,
                "files": [],
                "test_cases": [],
            },
        }
        proposal_file.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

        store = ProposalStore()
        assert store.update_status("opt-crossday", "approved")

        updated = store.get_proposal("opt-crossday")
        assert updated is not None
        assert updated["status"] == "approved"


class TestManualApply:
    @pytest.mark.asyncio
    async def test_manual_apply_without_auto_apply(
        self, tmp_path: Path, proposal_output_dir: Path
    ) -> None:
        install_test_config(
            tmp_path,
            {
                "self_optimization": {
                    "proposal_output_dir": str(proposal_output_dir),
                    "auto_apply": False,
                }
            },
        )

        target_file = tmp_path / "new_module.py"
        proposal = OptimizationProposal(
            id="opt-manual-001",
            type="add",
            risk_level="low",
            target=str(target_file),
            description="create file",
            rationale="test",
            expected_benefit="test",
            estimated_effort=1,
            files=[
                FileChange(
                    path="new_module.py",
                    action="create",
                    content="# test\n",
                    reason="test",
                )
            ],
        )

        store = ProposalStore()
        store.save_proposal(proposal)

        result = await store.apply_proposal_async(
            "opt-manual-001",
            root=str(tmp_path),
            manual=True,
        )
        assert result.status == "success"
        assert target_file.read_text(encoding="utf-8") == "# test\n"


class TestEmptyProposalSkip:
    @pytest.mark.asyncio
    async def test_apply_skips_advisory_proposal(self) -> None:
        proposal = OptimizationProposal(
            id="opt-empty",
            type="optimize",
            risk_level="low",
            target="工具: slow_tool",
            description="advisory only",
            rationale="",
            expected_benefit="",
            estimated_effort=10,
        )
        result = await apply_proposal(proposal)
        assert result.status == "skipped"
        assert "无可执行" in (result.error or "")


class TestLoopDetection:
    def test_detect_repeated_tool(self) -> None:
        events = [
            {
                "type": EVENT_TOOL_END,
                "session_key": "s1",
                "tool": "read_file",
            }
        ] * 6
        loops = _detect_loop_patterns(events)
        assert any(
            entry["type"] == "repeated_tool" and entry["tool"] == "read_file"
            for entry in loops
        )

    def test_detect_ping_pong(self) -> None:
        tools = ["read_file", "write_file"] * 3
        events = [
            {"type": EVENT_TOOL_END, "session_key": "s1", "tool": t} for t in tools
        ]
        loops = _detect_loop_patterns(events)
        assert any(entry["type"] == "ping_pong" for entry in loops)


class TestHistoryIndex:
    def test_save_proposal_updates_history(
        self, tmp_path: Path, proposal_output_dir: Path
    ) -> None:
        install_test_config(
            tmp_path,
            {"self_optimization": {"proposal_output_dir": str(proposal_output_dir)}},
        )
        store = ProposalStore()
        proposal = OptimizationProposal(
            id="opt-hist-001",
            type="optimize",
            risk_level="low",
            target="test",
            description="history test",
            rationale="",
            expected_benefit="",
            estimated_effort=5,
        )
        store.save_proposal(proposal)

        history_file = get_history_file()
        assert history_file.exists()
        index = json.loads(history_file.read_text(encoding="utf-8"))
        assert any(e["id"] == "opt-hist-001" for e in index)


class TestShlexCommand:
    @pytest.mark.asyncio
    async def test_run_validation_with_spaced_command(self, tmp_path: Path) -> None:
        proposal = OptimizationProposal(
            id="opt-cmd",
            type="optimize",
            risk_level="low",
            target="test",
            description="run echo",
            rationale="",
            expected_benefit="",
            estimated_effort=1,
            test_cases=[
                OptTestCase(
                    id="tc-echo",
                    type="unit",
                    description="echo test",
                    command="python -c \"print('ok')\"",
                )
            ],
        )
        result = await apply_proposal(proposal, root=str(tmp_path))
        assert result.status == "success"
        assert result.test_summary is not None
        assert result.test_summary.passed == 1
