"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from pathlib import Path

from miniagent.assistant.self_opt.proposal_engine import _pain_point_to_proposal
from miniagent.assistant.self_opt.types import PainPoint


def test_low_severity_pain_point_produces_low_risk_proposal(tmp_path: Path) -> None:
    proposal = _pain_point_to_proposal(
        PainPoint(
            category="testing",
            description="small issue",
            severity=1,
            frequency=1,
            suggestion="fix it",
        ),
        root=str(tmp_path),
    )
    assert proposal is not None
    assert proposal.risk_level == "low"
