"""执行器默认轮次与思考片段分隔（环境未设置时）。"""

from __future__ import annotations

import pytest


def test_step_max_turns_cap_default_48(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_EXECUTION_STEP_MAX_TURNS", raising=False)
    from miniagent.core import executor

    assert executor._step_max_turns_cap() == 48


def test_thinking_segment_separator_default_double_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINIAGENT_EXECUTION_THINKING_SEPARATOR", raising=False)
    from miniagent.core import executor

    assert executor._thinking_segment_separator() == "\n\n"


def test_thinking_segment_separator_env_backslash_n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_EXECUTION_THINKING_SEPARATOR", "\\n\\n---\\n\\n")
    from miniagent.core import executor

    assert executor._thinking_segment_separator() == "\n\n---\n\n"
