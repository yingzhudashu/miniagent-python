"""Focused regressions migrated from test_core_helper_edge_matrix.py."""

from __future__ import annotations

from pathlib import Path

from miniagent.agent.execution_prompts import build_current_turn_user_context
from miniagent.agent.execution_turn import StreamingBuffer


def test_execution_prompt_and_stream_consolidation(tmp_path: Path) -> None:
    context = build_current_turn_user_context(
        user_input=" run ",
        plan_summary="plan",
        keyword_context="memory",
        kb_context="kb",
        session_files_root=str(tmp_path),
        risk_level="high",
        current_time_context="now",
        output_spec_block=" JSON ",
    )
    assert all(
        fragment in context
        for fragment in ("执行计划摘要", "JSON", "相关记忆", "相关知识库", "high", "now")
    )

    buffer = StreamingBuffer()
    for _ in range(51):
        buffer.append("x")
    assert buffer.getvalue() == "x" * 51
    buffer.append("tail")
    assert buffer.getvalue().endswith("tail")
    assert len(buffer) == 55
    buffer.clear()
    assert buffer.getvalue() == "" and len(buffer) == 0
