"""LLM 参数解析。"""


from miniagent.agent.config import get_default_agent_config, merge_agent_config
from miniagent.agent.llm_params import (
    resolve_exec_completion_kwargs,
    resolve_planner_completion_kwargs,
)
from tests.config_helpers import install_test_config


def test_resolve_exec_uses_llm_overrides() -> None:
    base = get_default_agent_config()
    cfg = merge_agent_config(
        base,
        {"llm_overrides": {"profile": "test", "temperature": 0.1, "max_tokens": 100}},
    )
    kw = resolve_exec_completion_kwargs(cfg, stream=True)
    assert "model" not in kw
    assert kw["temperature"] == 0.1
    assert kw["max_tokens"] == 100
    assert "stream" not in kw


def test_resolve_exec_keeps_thinking_provider_neutral(tmp_path) -> None:
    install_test_config(tmp_path)
    base = get_default_agent_config()
    cfg = merge_agent_config(
        base,
        {
            "llm_overrides": {
                "thinking_level": "medium",
                "thinking_budget": 2048,
            }
        },
    )
    kw = resolve_exec_completion_kwargs(cfg, stream=False)
    assert kw["_thinking_level"] == "medium"
    assert kw["_thinking_budget"] == 2048
    assert "extra_body" not in kw


def test_resolve_planner_prefers_planner_keys() -> None:
    base = get_default_agent_config()
    cfg = merge_agent_config(
        base,
        {
            "llm_overrides": {
                "planner_temperature": 0.05,
                "planner_max_tokens": 512,
                "temperature": 0.9,
            }
        },
    )
    kw = resolve_planner_completion_kwargs(cfg)
    assert kw["temperature"] == 0.05
    assert kw["max_tokens"] == 512
    assert "stream" not in kw


def test_resolve_planner_none_uses_defaults() -> None:
    kw = resolve_planner_completion_kwargs(None)
    assert "model" not in kw
    assert kw["temperature"] == 0.3
