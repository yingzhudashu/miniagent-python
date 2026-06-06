"""工具意图截断测试。"""

from __future__ import annotations

import pytest


def test_extract_tool_intent_truncation_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.core.executor import _extract_tool_intent, _reset_env_caches_for_tests

    monkeypatch.setattr("miniagent.core.executor.EXECUTION_TOOL_INTENT_MAX_CHARS", 8)
    _reset_env_caches_for_tests()
    long_cmd = "x" * 40
    s = _extract_tool_intent("exec_command", {"command": long_cmd})
    assert s.startswith("执行命令: xxxxxxxx")
    assert "…（共 40 字）" in s


def test_extract_tool_intent_zero_means_no_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.core.executor import _extract_tool_intent, _reset_env_caches_for_tests

    monkeypatch.setattr("miniagent.core.executor.EXECUTION_TOOL_INTENT_MAX_CHARS", 0)
    _reset_env_caches_for_tests()
    long_cmd = "y" * 500
    s = _extract_tool_intent("exec_command", {"command": long_cmd})
    assert s == f"执行命令: {long_cmd}"
