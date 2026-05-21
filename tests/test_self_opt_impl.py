"""Tests for miniagent.core.self_opt inspector and proposal_engine implementation."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.core.self_opt.inspector import (
    _count_lines,
    _count_python_files,
    _estimate_test_coverage,
)
from miniagent.core.self_opt.proposal_engine import (
    _generate_proposal_id,
    _generate_test_proposals,
    _pain_point_to_proposal,
)
from miniagent.core.self_opt.types import InspectionReport, PainPoint


@pytest.fixture
def sample_project(tmp_path: Path) -> str:
    """Create a mini project structure for testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    (src / "utils.py").write_text("def helper(): pass\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_main(): pass\n", encoding="utf-8")
    return str(tmp_path)


def test_count_python_files(sample_project: str) -> None:
    count = _count_python_files(sample_project)
    assert count == 3  # main.py, utils.py, test_main.py


def test_count_lines(sample_project: str) -> None:
    lines = _count_lines(sample_project)
    assert lines >= 3  # at least 3 lines (1 per file)


def test_estimate_test_coverage(sample_project: str) -> None:
    coverage = _estimate_test_coverage(sample_project)
    # 1 test file out of 3 total = ~33%
    assert 0.0 <= coverage <= 100.0


def test_estimate_test_coverage_empty(tmp_path: Path) -> None:
    coverage = _estimate_test_coverage(str(tmp_path))
    assert coverage == 0.0


def test_generate_proposal_id() -> None:
    pid = _generate_proposal_id()
    assert len(pid) > 0


def test_pain_point_to_proposal(tmp_path: Path) -> None:
    pain = PainPoint(
        category="testing",
        description="Low test coverage in core module",
        severity=4,
        frequency=3,
        suggestion="Add unit tests",
    )
    proposal = _pain_point_to_proposal(pain, root=str(tmp_path))
    assert proposal is not None
    assert len(proposal.description) > 0


def test_generate_test_proposals() -> None:
    report = InspectionReport(
        timestamp="2026-05-20T00:00:00",
        version="1.0.0",
        summary="OK",
        metrics=[],
        pain_points=[],
        modules=[],
    )
    proposals = _generate_test_proposals(report)
    assert isinstance(proposals, list)
