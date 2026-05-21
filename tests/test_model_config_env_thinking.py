"""AGENT_THINKING_DEFAULT / OPENAI_THINKING_BUDGET 与 get_default_model_config 合并。"""

import pytest

from miniagent.core.config import MODEL_PROFILES, get_default_model_config


def test_agent_thinking_default_overrides_model_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
    monkeypatch.setenv("MODEL_PROFILE", "fast")
    monkeypatch.setenv("AGENT_THINKING_DEFAULT", "medium")
    mc = get_default_model_config()
    assert mc.thinking_level == "medium"
    assert mc.thinking_budget == 8192


def test_openai_thinking_budget_overrides_derived_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_THINKING_DEFAULT", "high")
    monkeypatch.setenv("OPENAI_THINKING_BUDGET", "12345")
    mc = get_default_model_config()
    assert mc.thinking_level == "heavy"
    assert mc.thinking_budget == 12345


def test_env_context_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
    monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
    monkeypatch.setenv("MODEL_PROFILE", "balanced")
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("OPENAI_MAX_TOKENS", raising=False)
    mc = get_default_model_config()
    assert mc.context_window == 128000
    assert mc.max_tokens == MODEL_PROFILES["balanced"].max_tokens

    monkeypatch.setenv("AGENT_CONTEXT_WINDOW", "40000")
    monkeypatch.setenv("OPENAI_MAX_TOKENS", "9000")
    mc2 = get_default_model_config()
    assert mc2.context_window == 40000
    assert mc2.max_tokens == 9000
