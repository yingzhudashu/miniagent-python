"""LLM 参数解析。"""

import pytest

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.core.llm_params import (
    resolve_exec_completion_kwargs,
    resolve_planner_completion_kwargs,
)


def test_resolve_exec_uses_model_overrides() -> None:
    base = get_default_agent_config()
    cfg = merge_agent_config(
        base,
        {"model_overrides": {"model": "gpt-test", "temperature": 0.1, "max_tokens": 100}},
    )
    kw = resolve_exec_completion_kwargs(cfg, stream=True)
    assert kw["model"] == "gpt-test"
    assert kw["temperature"] == 0.1
    assert kw["max_tokens"] == 100
    assert kw["stream"] is True


def test_resolve_exec_dashscope_includes_thinking_extra_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MINIAGENT_MODEL_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    base = get_default_agent_config()
    cfg = merge_agent_config(
        base,
        {
            "model_overrides": {
                "thinking_level": "medium",
                "thinking_budget": 2048,
            }
        },
    )
    kw = resolve_exec_completion_kwargs(cfg, stream=False)
    assert "extra_body" in kw
    assert kw["extra_body"].get("enable_thinking") is True
    assert kw["extra_body"].get("thinking_budget") == 2048


def test_resolve_planner_prefers_planner_keys() -> None:
    base = get_default_agent_config()
    cfg = merge_agent_config(
        base,
        {
            "model_overrides": {
                "planner_temperature": 0.05,
                "planner_max_tokens": 512,
                "temperature": 0.9,
            }
        },
    )
    kw = resolve_planner_completion_kwargs(cfg)
    assert kw["temperature"] == 0.05
    assert kw["max_tokens"] == 512
    assert kw["stream"] is False


def test_resolve_planner_none_uses_defaults() -> None:
    kw = resolve_planner_completion_kwargs(None)
    assert "model" in kw
    assert kw["temperature"] == 0.3
