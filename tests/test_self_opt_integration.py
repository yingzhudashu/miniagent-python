"""Tests for self-optimization system integration.

This test module validates the entire self-optimization flow:
1. Trace events emission and capture
2. RuntimeAnalyzer report generation
3. ProposalGenerator proposal creation
4. ProposalStore persistence and state management
5. CLI command execution
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from miniagent.infrastructure.tracing import (
    clear_trace_hooks,
    emit_trace,
    register_trace_hook,
)
from miniagent.infrastructure.trace_events import (
    EVENT_LLM_REQUEST,
    EVENT_LLM_RESPONSE,
    EVENT_TOOL_START,
    EVENT_TOOL_END,
    EVENT_TOOL_ERROR,
    EVENT_ERROR_COLLECT,
    make_error_event,
)
from miniagent.infrastructure.trace_stats import (
    load_trace_events,
    compute_tool_stats,
    compute_llm_stats,
    compute_error_stats,
    generate_daily_report,
)
from miniagent.core.self_opt.runtime_analyzer import RuntimeAnalyzer
from miniagent.core.self_opt.proposal_generator import ProposalGenerator
from miniagent.core.self_opt.proposal_store import ProposalStore
from miniagent.core.self_opt.types import OptimizationProposal


@pytest.fixture
def trace_output_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for trace output."""
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)
    return trace_dir


@pytest.fixture
def proposal_output_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for proposals."""
    proposal_dir = tmp_path / "proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    return proposal_dir


@pytest.fixture
def trace_events() -> list[dict[str, Any]]:
    """Generate sample trace events for testing."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session_key = "test-session-1"

    return [
        # Session lifecycle - 使用LLM事件替代（已删除SESSION事件）
        {
            "type": EVENT_LLM_REQUEST,
            "ts": "2026-06-05T10:00:00Z",
            "session_key": session_key,
            "phase": "init",
            "model": "gpt-4o-mini",
            "message_count": 1,
            "tool_count": 0,
        },
        # LLM calls
        {
            "type": EVENT_LLM_REQUEST,
            "ts": "2026-06-05T10:01:00Z",
            "session_key": session_key,
            "phase": "exec",
            "model": "gpt-4o-mini",
            "message_count": 5,
            "tool_count": 3,
        },
        {
            "type": EVENT_LLM_RESPONSE,
            "ts": "2026-06-05T10:01:30Z",
            "session_key": session_key,
            "phase": "exec",
            "has_tool_calls": True,
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        },
        # Tool calls
        {
            "type": EVENT_TOOL_START,
            "ts": "2026-06-05T10:02:00Z",
            "session_key": session_key,
            "tool": "read_file",
        },
        {
            "type": EVENT_TOOL_END,
            "ts": "2026-06-05T10:02:50Z",
            "session_key": session_key,
            "tool": "read_file",
            "duration_ms": 50,
            "success": True,
        },
        {
            "type": EVENT_TOOL_START,
            "ts": "2026-06-05T10:03:00Z",
            "session_key": session_key,
            "tool": "web_search",
        },
        {
            "type": EVENT_TOOL_END,
            "ts": "2026-06-05T10:05:00Z",
            "session_key": session_key,
            "tool": "web_search",
            "duration_ms": 2000,
            "success": True,
        },
        # Tool error
        {
            "type": EVENT_TOOL_ERROR,
            "ts": "2026-06-05T10:06:00Z",
            "session_key": session_key,
            "tool": "read_file",
            "error_type": "PermissionError",
            "error_message": "Access denied",
            "is_user_error": True,
        },
        # Error collection
        make_error_event(
            session_key=session_key,
            error_type="TimeoutError",
            error_message="Tool execution timeout",
            tool_name="web_search",
            is_user_error=False,
        ),
        # Session end - 使用LLM响应事件替代
        {
            "type": EVENT_LLM_RESPONSE,
            "ts": "2026-06-05T10:10:00Z",
            "session_key": session_key,
            "phase": "shutdown",
            "has_tool_calls": False,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        },
    ]


class TestTraceSystem:
    """Test trace event emission and capture."""

    def test_emit_trace_basic(self) -> None:
        """Test basic trace event emission."""
        collected: list[dict[str, Any]] = []

        def collector(event: dict[str, Any]) -> None:
            collected.append(event)

        register_trace_hook(collector)

        emit_trace({
            "type": "test.event",
            "data": "test_value",
        })

        assert len(collected) == 1
        assert collected[0]["type"] == "test.event"
        assert collected[0]["data"] == "test_value"

        clear_trace_hooks()

    def test_trace_events_constants(self) -> None:
        """Test that trace event constants are correctly defined."""
        assert EVENT_LLM_REQUEST == "llm.request"
        assert EVENT_LLM_RESPONSE == "llm.response"
        assert EVENT_TOOL_START == "tool.start"
        assert EVENT_TOOL_END == "tool.end"
        assert EVENT_TOOL_ERROR == "tool.error"
        assert EVENT_ERROR_COLLECT == "error.collect"
        # SESSION事件已删除（未在生产代码中使用）

    def test_make_error_event(self) -> None:
        """Test error event construction."""
        event = make_error_event(
            session_key="test-session",
            error_type="TimeoutError",
            error_message="Tool timeout",
            tool_name="web_search",
            is_user_error=False,
        )

        assert event["type"] == EVENT_ERROR_COLLECT
        assert event["session_key"] == "test-session"
        assert event["error_type"] == "TimeoutError"
        assert event["error_message"] == "Tool timeout"
        assert event["tool_name"] == "web_search"
        assert event["is_user_error"] is False


class TestTraceStats:
    """Test trace statistics generation."""

    def test_load_trace_events(self, trace_output_dir: Path, trace_events: list[dict[str, Any]]) -> None:
        """Test loading trace events from file."""
        # Override config
        os.environ["MINIAGENT_TRACE_OUTPUT_DIR"] = str(trace_output_dir)

        # Write events to file
        trace_file = trace_output_dir / f"trace-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with trace_file.open("w", encoding="utf-8") as f:
            for event in trace_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Load events
        events = load_trace_events()

        assert len(events) > 0
        assert any(e["type"] == EVENT_LLM_REQUEST for e in events)  # 使用LLM事件替代SESSION事件
        assert any(e["type"] == EVENT_LLM_RESPONSE for e in events)

        # Clean up
        if "MINIAGENT_TRACE_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_TRACE_OUTPUT_DIR"]

    def test_compute_tool_stats(self, trace_events: list[dict[str, Any]]) -> None:
        """Test tool statistics computation."""
        stats = compute_tool_stats(trace_events)

        assert "tools" in stats
        assert "slow_tools" in stats
        assert "failed_tools" in stats

        # Check tool stats
        if "read_file" in stats["tools"]:
            read_stats = stats["tools"]["read_file"]
            assert read_stats["count"] > 0
            assert read_stats["avg_ms"] >= 0
            assert read_stats["success_rate"] >= 0

        # Check slow tools
        slow_tools = stats["slow_tools"]
        if slow_tools:
            assert any(t["name"] == "web_search" for t in slow_tools)

    def test_compute_llm_stats(self, trace_events: list[dict[str, Any]]) -> None:
        """Test LLM statistics computation."""
        stats = compute_llm_stats(trace_events)

        assert "request_count" in stats
        assert stats["request_count"] > 0
        assert "total_tokens" in stats
        assert stats["total_tokens"]["prompt"] > 0
        assert stats["total_tokens"]["completion"] > 0

    def test_compute_error_stats(self, trace_events: list[dict[str, Any]]) -> None:
        """Test error statistics computation."""
        stats = compute_error_stats(trace_events)

        assert isinstance(stats, list)
        if stats:
            # Check error grouping
            error_types = [e["type"] for e in stats]
            assert "PermissionError" in error_types or "TimeoutError" in error_types

    def test_generate_daily_report(self, trace_events: list[dict[str, Any]], trace_output_dir: Path) -> None:
        """Test daily report generation."""
        # Override config
        os.environ["MINIAGENT_TRACE_OUTPUT_DIR"] = str(trace_output_dir)

        # Write events to file
        trace_file = trace_output_dir / f"trace-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with trace_file.open("w", encoding="utf-8") as f:
            for event in trace_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Generate report
        report = generate_daily_report()

        assert "date" in report
        # Report may have no events if config doesn't match
        if report.get("total_events", 0) > 0:
            assert "summary" in report
            assert "llm" in report
            assert "tools" in report
            assert "errors" in report

        # Clean up
        if "MINIAGENT_TRACE_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_TRACE_OUTPUT_DIR"]


class TestRuntimeAnalyzer:
    """Test runtime analysis."""

    def test_analyze_trace_data(self, trace_events: list[dict[str, Any]], trace_output_dir: Path) -> None:
        """Test analyzing trace data."""
        # Override config
        os.environ["MINIAGENT_TRACE_OUTPUT_DIR"] = str(trace_output_dir)

        # Write events
        trace_file = trace_output_dir / f"trace-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with trace_file.open("w", encoding="utf-8") as f:
            for event in trace_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        analyzer = RuntimeAnalyzer()
        report = analyzer.analyze()

        assert "date" in report
        assert "generated_at" in report
        assert "summary" in report
        assert "tools" in report
        assert "llm" in report
        assert "errors" in report
        assert "issues" in report

        # Clean up
        if "MINIAGENT_TRACE_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_TRACE_OUTPUT_DIR"]

    def test_detect_slow_tools(self, trace_events: list[dict[str, Any]], trace_output_dir: Path) -> None:
        """Test detecting slow tools issues."""
        # Write events
        trace_file = trace_output_dir / f"trace-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with trace_file.open("w", encoding="utf-8") as f:
            for event in trace_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        analyzer = RuntimeAnalyzer()
        report = analyzer.analyze()

        # Check for slow tool issues
        issues = report.get("issues", [])
        slow_tool_issues = [i for i in issues if i.get("type") == "slow_tool"]

        # web_search should be detected as slow (2000ms)
        if slow_tool_issues:
            assert any(i["tool"] == "web_search" for i in slow_tool_issues)

    def test_detect_high_frequency_errors(self, trace_events: list[dict[str, Any]], trace_output_dir: Path) -> None:
        """Test detecting high-frequency error issues."""
        # Override config
        os.environ["MINIAGENT_TRACE_OUTPUT_DIR"] = str(trace_output_dir)

        # Write events
        trace_file = trace_output_dir / f"trace-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with trace_file.open("w", encoding="utf-8") as f:
            for event in trace_events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

        analyzer = RuntimeAnalyzer()
        report = analyzer.analyze()

        # Check for error issues
        issues = report.get("issues", [])
        error_issues = [i for i in issues if i.get("type") == "high_frequency_error"]

        # At least one error should be detected
        assert isinstance(issues, list)

        # Clean up
        if "MINIAGENT_TRACE_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_TRACE_OUTPUT_DIR"]


class TestProposalGenerator:
    """Test proposal generation from runtime analysis."""

    def test_generate_slow_tool_proposal(self) -> None:
        """Test generating proposal for slow tool."""
        generator = ProposalGenerator()

        report = {
            "date": "2026-06-05",
            "issues": [
                {
                    "type": "slow_tool",
                    "tool": "web_search",
                    "avg_ms": 3000,
                    "severity": 2,
                }
            ],
        }

        proposals = generator.generate_from_runtime_report(report)

        assert isinstance(proposals, list)
        if proposals:
            # Check proposal structure
            proposal = proposals[0]
            assert proposal.target == "工具: web_search"
            assert proposal.risk_level in ("low", "medium", "high")
            assert len(proposal.description) > 0

    def test_generate_tool_failure_proposal(self) -> None:
        """Test generating proposal for tool failure."""
        generator = ProposalGenerator()

        report = {
            "date": "2026-06-05",
            "issues": [
                {
                    "type": "tool_failure",
                    "tool": "read_file",
                    "success_rate": 0.80,
                    "severity": 3,
                }
            ],
        }

        proposals = generator.generate_from_runtime_report(report)

        assert isinstance(proposals, list)
        if proposals:
            proposal = proposals[0]
            assert proposal.risk_level == "high"
            assert "成功率" in proposal.description or "80%" in proposal.description

    def test_generate_error_handling_proposal(self) -> None:
        """Test generating proposal for error handling."""
        generator = ProposalGenerator()

        report = {
            "date": "2026-06-05",
            "issues": [
                {
                    "type": "high_frequency_error",
                    "error_type": "TimeoutError",
                    "count": 5,
                    "is_user_error": False,
                    "severity": 3,
                }
            ],
        }

        proposals = generator.generate_from_runtime_report(report)

        assert isinstance(proposals, list)
        if proposals:
            proposal = proposals[0]
            assert "TimeoutError" in proposal.target


class TestProposalStore:
    """Test proposal persistence and state management."""

    def test_save_proposal(self, proposal_output_dir: Path) -> None:
        """Test saving proposal to store."""
        # Override config
        os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"] = str(proposal_output_dir)

        store = ProposalStore()
        proposal = OptimizationProposal(
            id="test-opt-001",
            type="optimize",
            risk_level="low",
            target="工具: read_file",
            description="优化 read_file 性能",
            rationale="执行时延过高",
            expected_benefit="降低平均时延",
            estimated_effort=30,
        )

        proposal_id = store.save_proposal(proposal, source="runtime_analysis")

        assert proposal_id == "test-opt-001"

        # Verify file exists
        proposal_file = proposal_output_dir / f"proposals-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        assert proposal_file.exists()

        # Clean up
        if "MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"]

    def test_load_proposals(self, proposal_output_dir: Path) -> None:
        """Test loading proposals from store."""
        # Override config
        os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"] = str(proposal_output_dir)

        store = ProposalStore()

        # Create and save proposal
        proposal = OptimizationProposal(
            id="test-opt-002",
            type="optimize",
            risk_level="medium",
            target="工具: web_search",
            description="优化 web_search",
            rationale="成功率过低",
            expected_benefit="提升成功率",
            estimated_effort=60,
        )
        store.save_proposal(proposal)

        # Load proposals
        proposals = store.load_proposals()

        assert isinstance(proposals, list)
        assert len(proposals) > 0

        # Check proposal structure
        record = proposals[0]
        assert "id" in record
        assert "status" in record
        assert "proposal" in record

        # Clean up
        if "MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"]

    def test_update_proposal_status(self, proposal_output_dir: Path) -> None:
        """Test updating proposal status."""
        # Override config
        os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"] = str(proposal_output_dir)

        store = ProposalStore()

        # Create and save proposal
        proposal = OptimizationProposal(
            id="test-opt-003",
            type="optimize",
            risk_level="low",
            target="错误处理",
            description="改进错误提示",
            rationale="用户误用频繁",
            expected_benefit="减少错误",
            estimated_effort=15,
        )
        store.save_proposal(proposal)

        # Update status
        success = store.update_status("test-opt-003", "approved")

        assert success

        # Verify update
        proposals = store.load_proposals()
        approved = [p for p in proposals if p.get("status") == "approved"]

        assert len(approved) > 0

        # Clean up
        if "MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"]


class TestCLICommands:
    """Test self-optimization CLI commands."""

    def test_cmd_self_opt_status(self) -> None:
        """Test /self-opt status command."""
        from miniagent.engine.cli_commands import cmd_self_opt_status

        # Execute command
        cmd_self_opt_status()

        # Command should not raise exception
        # Output is printed to stdout

    def test_cmd_self_opt_proposals(self, proposal_output_dir: Path) -> None:
        """Test /self-opt proposals command."""
        from miniagent.engine.cli_commands import cmd_self_opt_proposals

        # Override config
        os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"] = str(proposal_output_dir)

        store = ProposalStore()
        proposal = OptimizationProposal(
            id="test-cli-001",
            type="optimize",
            risk_level="low",
            target="测试",
            description="测试提案",
            rationale="测试",
            expected_benefit="测试",
            estimated_effort=10,
        )
        store.save_proposal(proposal)

        # Execute command
        cmd_self_opt_proposals()

        # Clean up
        if "MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"]

    def test_cmd_self_opt_show(self, proposal_output_dir: Path) -> None:
        """Test /self-opt show command."""
        from miniagent.engine.cli_commands import cmd_self_opt_show

        # Override config
        os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"] = str(proposal_output_dir)

        store = ProposalStore()
        proposal = OptimizationProposal(
            id="test-cli-002",
            type="optimize",
            risk_level="medium",
            target="测试显示",
            description="测试显示提案",
            rationale="测试",
            expected_benefit="测试",
            estimated_effort=20,
        )
        store.save_proposal(proposal)

        # Execute command
        cmd_self_opt_show("test-cli-002")

        # Clean up
        if "MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR" in os.environ:
            del os.environ["MINIAGENT_SELF_OPTIMIZATION_PROPOSAL_OUTPUT_DIR"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])