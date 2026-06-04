"""Tests for Thinking System - Merged from multiple test files.

Covers:
- Thinking presets (level mappings)
- Thinking numbering (turn/step counters)
- Thinking merge tools (tool line merging)
- Thinking callback and headers
- Thinking stream indent
- Thinking CLI width

Original files merged:
- test_thinking_presets.py
- test_thinking_numbering.py
- test_thinking_merge_tools.py
- test_thinking_callback_and_executor_headers.py
- test_thinking_stream_indent.py
- test_thinking_cli_width.py
"""

from __future__ import annotations

import importlib.util
import pytest

from miniagent.core.executor import _step_thinking_header
from miniagent.core.thinking_callback import invoke_on_thinking
from miniagent.core.thinking_presets import (
    THINKING_LEVEL_PRESETS,
    map_business_depth,
    map_thinking_level_to_model,
)
from miniagent.engine.thinking import ThinkingDisplay, indent_stream_thinking_suffix
from miniagent.types.planning import PlanStep

# Check if prompt_toolkit is available
_HAS_PROMPT_TOOLKIT = importlib.util.find_spec("prompt_toolkit") is not None


# ============================================================================
# Thinking Presets Tests
# ============================================================================


class TestMapThinkingLevelToModel:
    """map_thinking_level_to_model 将档位映射为 (level, budget)。"""

    def test_low(self):
        assert map_thinking_level_to_model("low") == ("light", 1024)

    def test_medium(self):
        assert map_thinking_level_to_model("medium") == ("medium", 8192)

    def test_high(self):
        assert map_thinking_level_to_model("high") == ("heavy", 81920)

    def test_case_insensitive(self):
        assert map_thinking_level_to_model("LOW") == ("light", 1024)
        assert map_thinking_level_to_model("Medium") == ("medium", 8192)

    def test_unknown_defaults_to_medium(self):
        assert map_thinking_level_to_model("unknown") == ("medium", 8192)

    def test_none_defaults_to_medium(self):
        assert map_thinking_level_to_model(None) == ("medium", 8192)  # type: ignore[arg-type]

    def test_empty_string_defaults_to_medium(self):
        assert map_thinking_level_to_model("") == ("medium", 8192)


class TestMapBusinessDepth:
    """map_business_depth 将规划/步骤 thinkingLevel 映射为 (level, budget)。"""

    @pytest.mark.parametrize(
        "inputs,expected",
        [
            (["simple", "low", "轻", "低"], ("light", 1024)),
            (["normal", "medium", "中", "一般"], ("medium", 8192)),
            (["high", "complex", "重", "高", "复杂"], ("heavy", 81920)),
        ],
    )
    def test_known_levels(self, inputs, expected):
        for inp in inputs:
            assert map_business_depth(inp) == expected

    def test_none_defaults_to_medium(self):
        assert map_business_depth(None) == ("medium", 8192)

    def test_empty_string_defaults_to_medium(self):
        assert map_business_depth("") == ("medium", 8192)

    def test_unknown_defaults_to_medium(self):
        assert map_business_depth("foobar") == ("medium", 8192)

    def test_whitespace_stripped(self):
        assert map_business_depth("  LOW  ") == ("light", 1024)


class TestThinkingLevelPresetsConstant:
    """THINKING_LEVEL_PRESETS 常量结构验证。"""

    def test_has_all_keys(self):
        assert set(THINKING_LEVEL_PRESETS.keys()) == {"low", "medium", "high"}

    def test_values_are_tuples(self):
        for key, val in THINKING_LEVEL_PRESETS.items():
            assert isinstance(val, tuple)
            assert len(val) == 2
            assert isinstance(val[0], str)
            assert isinstance(val[1], int)


# ============================================================================
# Thinking Numbering Tests
# ============================================================================


class TestThinkingNumbering:
    """ThinkingDisplay 编号系统测试。"""

    def test_next_turn_persistent_across_reset(self) -> None:
        """next_turn 持久递增，不随 reset_counter 清零。"""
        td = ThinkingDisplay()
        assert td.next_turn("") == 1
        assert td.next_turn("") == 2
        td.reset_counter("")
        assert td.next_turn("") == 3

    def test_step_counter_resets_per_turn(self) -> None:
        """step_counter 在 reset_counter 后从零重新开始。"""
        td = ThinkingDisplay()
        assert td._next_step("") == 0
        assert td._next_step("") == 1
        td.reset_counter("")
        assert td._next_step("") == 0

    def test_turn_number_per_session_isolation(self) -> None:
        """不同 session_key 的 turn_number 独立。"""
        td = ThinkingDisplay()
        assert td.next_turn("session_a") == 1
        assert td.next_turn("session_b") == 1
        assert td.next_turn("session_a") == 2
        assert td.next_turn("session_b") == 2

    def test_step_counter_per_session_isolation(self) -> None:
        """不同 session_key 的 step_counter 独立。"""
        td = ThinkingDisplay()
        td._next_step("a")
        td._next_step("a")
        assert td._next_step("b") == 0

    def test_reset_counter_clears_stream_state(self) -> None:
        """reset_counter 清除流式状态。"""
        td = ThinkingDisplay()
        td.reset_counter("")
        state = td._get_state("")
        state.stream_step = 5
        state.stream_header = "[执行]"
        state.stream_done = True
        td.reset_counter("")
        assert state.stream_step is None
        assert state.stream_header == ""
        assert state.stream_done is False

    def test_reset_counter_preserves_turn_number(self) -> None:
        """reset_counter 不改变 turn_number。"""
        td = ThinkingDisplay()
        td.next_turn("")
        td.next_turn("")
        td.reset_counter("")
        state = td._get_state("")
        assert state.turn_number == 2


# ============================================================================
# Thinking Merge Tools Tests
# ============================================================================


class TestThinkingMergeTools:
    """合并同轮工具行与思考展示的回归测试。"""

    @pytest.mark.asyncio
    async def test_thinking_display_merge_tool_no_second_step_label(self):
        td = ThinkingDisplay()
        sink: list[tuple[str, str]] = []

        def capture(text: str, kind: str = "chunk") -> None:
            sink.append((text, kind))

        td.set_output_sink(capture)
        label = "[第 1 轮]"
        await td.show(label, streaming=True, header=label)
        await td.show(label + "思考正文", streaming=True, header=label)
        await td.show("🔧 web_search — intent", streaming=False, header=label)

        label_lines = [t for t, k in sink if k == "label"]
        assert len(label_lines) == 1
        assert "[第 1 轮]" in label_lines[0]

        chunks = [t for t, k in sink if k == "chunk"]
        joined = "".join(chunks)
        assert "🔧 web_search" in joined
        assert "思考正文" in joined

    @pytest.mark.asyncio
    async def test_thinking_display_merge_two_tools_same_round_one_label(self):
        """同一轮连续两次工具行仍只打一条轮次 label。"""
        td = ThinkingDisplay()
        sink: list[tuple[str, str]] = []

        def capture(text: str, kind: str = "chunk") -> None:
            sink.append((text, kind))

        td.set_output_sink(capture)
        label = "[第 1 轮]"
        await td.show(label, streaming=True, header=label)
        await td.show(label + "正文", streaming=True, header=label)
        await td.show("🔧 tool_a — x", streaming=False, header=label)
        await td.show("🔧 tool_b — y", streaming=False, header=label)

        label_lines = [t for t, k in sink if k == "label"]
        assert len(label_lines) == 1

        chunks = "".join(t for t, k in sink if k == "chunk")
        assert "tool_a" in chunks and "tool_b" in chunks

    @pytest.mark.asyncio
    async def test_thinking_display_merge_disabled_extra_label(self, monkeypatch):
        monkeypatch.setenv("MINIAGENT_EXECUTION_THINKING_MERGE_TOOLS", "0")
        td = ThinkingDisplay()
        sink: list[tuple[str, str]] = []

        def capture(text: str, kind: str = "chunk") -> None:
            sink.append((text, kind))

        td.set_output_sink(capture)
        label = "[第 1 轮]"
        await td.show(label, streaming=True, header=label)
        await td.show(label + "x", streaming=True, header=label)
        await td.show("🔧 t — i", streaming=False, header=label)

        label_lines = [t for t, k in sink if k == "label"]
        assert len(label_lines) == 2

    @pytest.mark.asyncio
    async def test_cli_phase_changed_resets_stream_without_feishu(self) -> None:
        """纯 CLI（无飞书）：流式 header 切换时收尾并重置。"""
        td = ThinkingDisplay()
        sink: list[tuple[str, str]] = []

        def capture(text: str, kind: str = "chunk") -> None:
            sink.append((text, kind))

        td.set_output_sink(capture)
        h_plan = "[评估与计划]"
        h_exec = "[执行]"
        await td.show(h_plan, streaming=True, header=h_plan)
        await td.show("planning body", streaming=True, header=h_plan)
        await td.show("exec body", streaming=True, header=h_exec)

        label_lines = [t for t, k in sink if k == "label"]
        assert len(label_lines) == 2
        assert h_plan in label_lines[0]
        assert h_exec in label_lines[1]

        chunks = "".join(t for t, k in sink if k == "chunk")
        assert "planning body" in chunks
        assert "exec body" in chunks

    @pytest.mark.asyncio
    async def test_cli_tools_merge_without_prior_streaming(self) -> None:
        """LLM 无正文仅工具调用时，首个工具行初始化流状态。"""
        td = ThinkingDisplay()
        sink: list[tuple[str, str]] = []

        def capture(text: str, kind: str = "chunk") -> None:
            sink.append((text, kind))

        td.set_output_sink(capture)
        label = "[执行]"

        await td.show("🔧 tool_a — x", streaming=False, header=label)
        await td.show("🔧 tool_b — y", streaming=False, header=label)

        label_lines = [t for t, k in sink if k == "label"]
        assert len(label_lines) == 1
        assert label in label_lines[0]

        chunks = "".join(t for t, k in sink if k == "chunk")
        assert "tool_a" in chunks and "tool_b" in chunks


# ============================================================================
# Thinking Callback Tests
# ============================================================================


class TestThinkingCallback:
    """thinking_callback 与分步思考 header 形状的单元测试。"""

    @pytest.mark.asyncio
    async def test_invoke_on_thinking_passes_full_record_with_var_keyword(self) -> None:
        received: list[object] = []

        async def cb(text: str, streaming: bool, header: str, **kwargs: object) -> None:
            received.append(kwargs.get("full_record"))

        await invoke_on_thinking(cb, "d", True, "h", full_record="FULL")
        assert received == ["FULL"]

    @pytest.mark.asyncio
    async def test_invoke_on_thinking_passes_full_record_named_param(self) -> None:
        received: list[str | None] = []

        async def cb(
            text: str, streaming: bool, header: str, *, full_record: str | None = None
        ) -> None:
            received.append(full_record)

        await invoke_on_thinking(cb, "d", False, "h", full_record="FULL")
        assert received == ["FULL"]

    @pytest.mark.asyncio
    async def test_invoke_on_thinking_three_arg_cb_ignores_full_record(self) -> None:
        calls: list[tuple[object, ...]] = []

        async def cb(text: str, streaming: bool, header: str) -> None:
            calls.append((text, streaming, header))

        await invoke_on_thinking(cb, "x", True, "y", full_record=None)
        assert calls == [("x", True, "y")]
        await invoke_on_thinking(cb, "a", False, "b", full_record="should_not_break")
        assert calls == [("x", True, "y"), ("a", False, "b")]

    def test_step_thinking_header_shape_and_truncation(self) -> None:
        long_desc = "字" * 80
        step = PlanStep(
            step_number=2,
            description=long_desc,
            required_toolboxes=[],
        )
        h = _step_thinking_header(0, 5, step)
        assert h.startswith("[步骤 2/5]")
        assert len(h) < len("[步骤 2/5] " + long_desc)


# ============================================================================
# Thinking Stream Indent Tests
# ============================================================================


class TestThinkingStreamIndent:
    """流式思考段首可选前缀测试。"""

    def test_indent_default_no_paragraph_prefix(self) -> None:
        full = "First line\nstill first paragraph\n\nSecond paragraph"
        assert indent_stream_thinking_suffix(full, 0) == full

    def test_indent_incremental_paragraph_boundary_default(self) -> None:
        acc1 = "A\n\n"
        assert indent_stream_thinking_suffix(acc1, 0) == "A\n\n"
        full = "A\n\nB"
        assert indent_stream_thinking_suffix(full, len(acc1)) == "B"

    def test_indent_full_body_matches_incremental_parts_default(self) -> None:
        full = "A\n\nB"
        assert indent_stream_thinking_suffix(full, 0) == full
        assert indent_stream_thinking_suffix(full[: len("A\n\n")], 0) == "A\n\n"
        assert indent_stream_thinking_suffix(full, len("A\n\n")) == "B"

    def test_indent_no_paragraph_break_mid_word(self) -> None:
        assert indent_stream_thinking_suffix("hello wo", 0) == "hello wo"
        assert indent_stream_thinking_suffix("hello world", len("hello wo")) == "rld"

    def test_indent_empty_suffix(self) -> None:
        assert indent_stream_thinking_suffix("x", 1) == ""
        assert indent_stream_thinking_suffix("", 0) == ""

    def test_indent_explicit_four_spaces_paragraph_starts(self) -> None:
        full = "First line\nstill first paragraph\n\nSecond paragraph"
        assert indent_stream_thinking_suffix(full, 0, indent="    ") == (
            "    First line\nstill first paragraph\n\n    Second paragraph"
        )
        acc1 = "A\n\n"
        assert indent_stream_thinking_suffix(acc1, 0, indent="    ") == "    A\n\n"
        full2 = "A\n\nB"
        assert indent_stream_thinking_suffix(full2, len(acc1), indent="    ") == "    B"


# ============================================================================
# Thinking CLI Width Tests
# ============================================================================


class TestThinkingCLIWidth:
    """ThinkingDisplay Rich 宽度与 main 回复区对齐测试。"""

    @pytest.mark.asyncio
    async def test_set_cli_markdown_width_used_for_thinking_rich(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINIAGENT_CLI_THINKING_RICH", "1")
        seen: list[int] = []

        def fake_render(markdown: str, *, width: int) -> str:
            seen.append(width)
            return "ok"

        monkeypatch.setattr(
            "miniagent.engine.markdown_cli.render_markdown_to_ansi",
            fake_render,
        )
        from miniagent.engine.thinking import ThinkingDisplay

        td = ThinkingDisplay()
        td.set_cli_markdown_width(lambda: 99)

        def sink(text: str, kind: str = "chunk", *, ansi_markdown: str | None = None) -> None:
            pass

        td.set_output_sink(sink)
        await td.show(
            "| a | b |\n|---|---|\n| 1 | 2 |\n",
            streaming=False,
            header="",
        )
        assert seen == [99]


__all__ = [
    "TestMapThinkingLevelToModel",
    "TestMapBusinessDepth",
    "TestThinkingLevelPresetsConstant",
    "TestThinkingNumbering",
    "TestThinkingMergeTools",
    "TestThinkingCallback",
    "TestThinkingStreamIndent",
    "TestThinkingCLIWidth",
]