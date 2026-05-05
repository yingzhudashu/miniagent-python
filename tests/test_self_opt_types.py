"""Tests for self-optimization types."""

import pytest
from src.core.self_opt.types import (
    OptTestCase,
    FileChange,
    OptimizationProposal,
    CodeQualityMetric,
    ModuleAnalysis,
    PainPoint,
    InspectionReport,
    OptimizationResult,
    OptTestSummary,
)


class TestTypesCreation:
    def test_test_case(self):
        tc = OptTestCase(
            id="tc-1",
            type="unit",
            description="Test something",
            setup="Set up env",
            action="Run function",
            expected="Returns True",
            command="pytest test_x.py",
        )
        assert tc.id == "tc-1"

    def test_file_change(self):
        fc = FileChange(path="src/test.py", action="create", content="print('hi')")
        assert fc.action == "create"

    def test_optimization_proposal(self):
        proposal = OptimizationProposal(
            id="prop-1",
            type="add",
            risk_level="low",
            target="src/new_module.py",
            description="Add new module",
            rationale="Based on inspection",
            expected_benefit="Better organization",
        )
        assert proposal.risk_level == "low"
        assert proposal.files == []

    def test_inspection_report(self):
        report = InspectionReport(
            timestamp="2026-05-06",
            version="0.1.0",
            summary="All good",
        )
        assert report.version == "0.1.0"

    def test_optimization_result(self):
        result = OptimizationResult(
            proposal_id="prop-1",
            status="success",
            test_summary=OptTestSummary(total=3, passed=3, failed=0),
        )
        assert result.status == "success"
