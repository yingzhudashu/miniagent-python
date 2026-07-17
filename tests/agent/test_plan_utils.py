"""plan_utils 与规划类型运行时辅助测试。"""

from __future__ import annotations

from miniagent.agent.plan_utils import (
    format_estimated_cost_block,
    format_output_spec_block,
    order_steps_by_dependencies,
    parse_plan_chunks_from_raw,
    parse_plan_steps_from_raw,
    resolve_chunk_compress_threshold,
    resolve_effective_overflow_strategy,
    resolve_execution_step_groups,
)
from miniagent.agent.planner import _dict_to_plan
from miniagent.agent.types.planning import (
    ContextStrategy,
    EstimatedCost,
    OutputSpec,
    PlanChunk,
    PlanStep,
    StructuredPlan,
    SuggestedConfig,
)


def _step_as_dict(s, idx):
    return s if isinstance(s, dict) else {"stepNumber": idx, "description": str(s)}


def _step_thinking(_s):
    return "medium"


def test_order_steps_by_dependencies() -> None:
    steps = [
        PlanStep(2, "分析", [], depends_on=1),
        PlanStep(1, "读取", []),
        PlanStep(3, "输出", [], depends_on=2),
    ]
    ordered = order_steps_by_dependencies(steps)
    assert [s.step_number for s in ordered] == [1, 2, 3]


def test_order_steps_cycle_preserves_input_order() -> None:
    steps = [
        PlanStep(1, "a", [], depends_on=2),
        PlanStep(2, "b", [], depends_on=1),
    ]
    ordered = order_steps_by_dependencies(steps)
    assert [s.step_number for s in ordered] == [1, 2]
    assert len(ordered) == 2


def test_order_steps_duplicate_step_numbers_keeps_both() -> None:
    steps = [
        PlanStep(1, "first", []),
        PlanStep(1, "second", [], depends_on=1),
    ]
    ordered = order_steps_by_dependencies(steps)
    assert [s.description for s in ordered] == ["first", "second"]


def test_order_steps_string_depends_on() -> None:
    raw = [
        {"stepNumber": 2, "description": "b", "dependsOn": "1"},
        {"stepNumber": 1, "description": "a"},
    ]
    steps = parse_plan_steps_from_raw(
        raw,
        step_as_dict=_step_as_dict,
        step_thinking_level=_step_thinking,
    )
    ordered = order_steps_by_dependencies(steps)
    assert [s.step_number for s in ordered] == [1, 2]


def test_order_steps_invalid_depends_on() -> None:
    steps = [
        PlanStep(1, "a", [], depends_on=99),
        PlanStep(2, "b", []),
    ]
    ordered = order_steps_by_dependencies(steps)
    assert [s.step_number for s in ordered] == [1, 2]


def test_resolve_execution_step_groups_prefers_chunks() -> None:
    plan = StructuredPlan(
        steps=[PlanStep(1, "flat step", [])],
        context_strategy=ContextStrategy(
            mode="chunked",
            chunks=[
                PlanChunk(
                    chunk_number=1,
                    steps=[PlanStep(2, "chunk step", [])],
                    chunk_system_prompt="chunk ctx",
                )
            ],
        ),
    )
    groups = resolve_execution_step_groups(plan)
    assert len(groups) == 1
    assert groups[0][0] == "chunk ctx"
    assert groups[0][1][0].description == "chunk step"


def test_resolve_execution_step_groups_empty_chunks_fallback() -> None:
    plan = StructuredPlan(
        steps=[PlanStep(1, "flat", [])],
        context_strategy=ContextStrategy(chunks=[PlanChunk(chunk_number=1, steps=[])]),
    )
    groups = resolve_execution_step_groups(plan)
    assert len(groups) == 1
    assert groups[0][1][0].description == "flat"


def test_resolve_execution_step_groups_multiple_chunks_sorted() -> None:
    plan = StructuredPlan(
        context_strategy=ContextStrategy(
            chunks=[
                PlanChunk(chunk_number=2, steps=[PlanStep(1, "second chunk", [])], chunk_system_prompt="b"),
                PlanChunk(chunk_number=1, steps=[PlanStep(1, "first chunk", [])], chunk_system_prompt="a"),
            ]
        )
    )
    groups = resolve_execution_step_groups(plan)
    assert len(groups) == 2
    assert groups[0][0] == "a"
    assert groups[0][1][0].description == "first chunk"
    assert groups[1][0] == "b"


def test_resolve_effective_overflow_strategy_from_mode() -> None:
    plan = StructuredPlan(
        context_strategy=ContextStrategy(mode="summarize"),
        suggested_config=SuggestedConfig(),
    )
    assert resolve_effective_overflow_strategy(plan, "error") == "summarize"


def test_resolve_effective_overflow_strategy_truncates() -> None:
    plan = StructuredPlan(context_strategy=ContextStrategy(mode="truncate"))
    assert resolve_effective_overflow_strategy(plan, "error") == "truncate"


def test_resolve_effective_overflow_strategy_suggested_config_wins() -> None:
    plan = StructuredPlan(
        context_strategy=ContextStrategy(mode="summarize"),
        suggested_config=SuggestedConfig(context_overflow_strategy="truncate"),
    )
    assert resolve_effective_overflow_strategy(plan, "error") == "truncate"


def test_resolve_effective_overflow_strategy_chunked_uses_default() -> None:
    plan = StructuredPlan(context_strategy=ContextStrategy(mode="chunked"))
    assert resolve_effective_overflow_strategy(plan, "error") == "error"


def test_resolve_chunk_compress_threshold() -> None:
    plan = StructuredPlan(suggested_config=SuggestedConfig(chunk_token_budget=32000))
    threshold = resolve_chunk_compress_threshold(
        plan, context_window=128000, default_threshold=0.6
    )
    assert threshold < 0.6
    assert threshold >= 0.25


def test_format_output_spec_and_cost_blocks() -> None:
    assert format_output_spec_block(OutputSpec()) is None
    assert format_estimated_cost_block(EstimatedCost()) is None
    out = format_output_spec_block(
        OutputSpec(language="zh-CN", format="markdown", expected_deliverable="报告")
    )
    assert out is not None
    assert "报告" in out
    assert "语言" not in out
    assert "格式" not in out
    cost = format_estimated_cost_block(EstimatedCost(input_tokens=100, total_usd=0.01))
    assert cost is not None
    assert "$0.0100" in cost


def test_format_output_spec_non_default_language() -> None:
    out = format_output_spec_block(OutputSpec(language="en-US"))
    assert out is not None
    assert "en-US" in out


def test_dict_to_plan_parses_context_chunks() -> None:
    data = {
        "summary": "分块任务",
        "steps": [],
        "contextStrategy": {
            "mode": "chunked",
            "reason": "任务较大",
            "chunks": [
                {
                    "chunkNumber": 1,
                    "chunkSystemPrompt": "第一块",
                    "steps": [
                        {
                            "stepNumber": 1,
                            "description": "步骤 A",
                            "requiredToolboxes": [],
                        }
                    ],
                }
            ],
        },
        "outputSpec": {"language": "en-US", "format": "text", "expectedDeliverable": "doc"},
        "estimatedCost": {"inputTokens": 10, "outputTokens": 20, "totalUSD": 0.001},
    }
    plan = _dict_to_plan(data)
    assert plan.context_strategy.mode == "chunked"
    assert plan.context_strategy.chunks is not None
    assert len(plan.context_strategy.chunks) == 1
    assert plan.context_strategy.chunks[0].chunk_system_prompt == "第一块"
    assert plan.output_spec.format == "text"
    assert plan.estimated_cost.total_usd == 0.001


def test_parse_plan_steps_from_raw() -> None:
    steps = parse_plan_steps_from_raw(
        [{"stepNumber": 3, "description": "x", "dependsOn": "2"}],
        step_as_dict=_step_as_dict,
        step_thinking_level=_step_thinking,
    )
    assert steps[0].step_number == 3
    assert steps[0].depends_on == 2
    assert steps[0].thinking_level == "medium"


def test_parse_plan_chunks_from_raw() -> None:
    chunks = parse_plan_chunks_from_raw(
        [{"chunkNumber": 2, "steps": [{"stepNumber": 1, "description": "x"}]}],
        step_as_dict=_step_as_dict,
        step_thinking_level=_step_thinking,
    )
    assert chunks is not None
    assert chunks[0].chunk_number == 2
    assert chunks[0].steps[0].description == "x"


def test_parse_plan_chunks_from_raw_all_invalid() -> None:
    chunks = parse_plan_chunks_from_raw(
        ["not dict", None],
        step_as_dict=_step_as_dict,
        step_thinking_level=_step_thinking,
    )
    assert chunks is None
