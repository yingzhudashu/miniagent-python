"""运行报告到优化提案的阈值、排序、去重与数量限制测试。"""

from __future__ import annotations

from miniagent.assistant.self_opt.proposal_generator import ProposalGenerator


def test_runtime_report_generates_all_supported_proposal_types(monkeypatch) -> None:
    import miniagent.assistant.self_opt.proposal_generator as module

    monkeypatch.setattr(
        module,
        "get_config",
        lambda key, default=None: {
            "self_optimization.min_duration_ms_threshold": 100,
            "self_optimization.min_failure_rate_threshold": 0.05,
        }.get(key, default),
    )
    report = {
        "issues": [
            {"type": "slow_tool", "tool": "slow", "avg_ms": 500},
            {"type": "tool_failure", "tool": "broken", "success_rate": 0.5},
            {"type": "high_frequency_error", "error_type": "ValueError", "count": 4, "is_user_error": True},
            {"type": "tool_loop", "tool": "loop", "count": 8},
            {"type": "ping_pong", "tools": ["a", "b"]},
            {"type": "context_pressure", "compress_count": 7},
        ],
        "llm": {
            "request_count": 20,
            "total_tokens": {"prompt": 120_000, "completion": 10_000},
        },
    }
    proposals = ProposalGenerator().generate_from_runtime_report(report, max_proposals=20)
    targets = {proposal.target for proposal in proposals}
    assert len(proposals) == 7
    assert {"工具: slow", "工具: broken", "LLM token 消耗", "上下文压缩频率"} <= targets
    assert [proposal.risk_level for proposal in proposals] == sorted(
        [proposal.risk_level for proposal in proposals],
        key={"low": 0, "medium": 1, "high": 2}.get,
    )


def test_runtime_report_ignores_below_threshold_and_malformed_issues() -> None:
    report = {
        "issues": [
            {"type": "slow_tool", "tool": "", "avg_ms": 9999},
            {"type": "tool_failure", "tool": "ok", "success_rate": 0.99},
            {"type": "high_frequency_error", "error_type": "", "count": 10},
            {"type": "tool_loop", "tool": "loop", "count": 2},
            {"type": "ping_pong", "tools": ["one"]},
            {"type": "context_pressure", "compress_count": 2},
            {"type": "unknown"},
        ],
        "llm": {"request_count": 1, "total_tokens": {"prompt": 1, "completion": 1}},
    }
    assert ProposalGenerator().generate_from_runtime_report(report) == []


def test_merge_proposals_prefers_higher_risk_and_limits() -> None:
    generator = ProposalGenerator()
    low = generator._make_token_optimization_proposal(
        {"request_count": 1, "total_tokens": {"prompt": 10, "completion": 2}}
    )
    high = generator._make_tool_failure_proposal(
        {"tool": "x", "success_rate": 0.1}
    )
    assert high is not None
    high.target = low.target
    other = generator._make_context_pressure_proposal({"compress_count": 6})
    assert other is not None

    merged = generator.merge_proposals([low], [high, other], max_total=2)
    assert len(merged) == 2
    assert high in merged and low not in merged

