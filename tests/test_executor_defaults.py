"""执行器默认轮次与思考片段分隔（Internal 常量）。"""

from __future__ import annotations


def test_step_max_turns_cap_default_48() -> None:
    from miniagent.core import executor

    assert executor._step_max_turns_cap() == 48


def test_thinking_segment_separator_default_double_newline() -> None:
    from miniagent.core import executor

    assert executor._thinking_segment_separator() == "\n\n"
