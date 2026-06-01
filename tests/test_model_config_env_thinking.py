"""MINIAGENT_MODEL_THINKING_LEVEL / MINIAGENT_MODEL_THINKING_BUDGET 与 get_default_model_config 合并。

环境变量命名规则：MINIAGENT_<SECTION_KEY>（如 MINIAGENT_MODEL_THINKING_LEVEL）
"""

import pytest

from miniagent.core.config import get_default_model_config
from miniagent.infrastructure.json_config import JsonConfigLoader


def test_agent_thinking_default_sets_thinking_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """MINIAGENT_MODEL_THINKING_LEVEL 设置thinking档位。"""
    monkeypatch.delenv("MINIAGENT_MODEL_THINKING_BUDGET", raising=False)
    monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
    monkeypatch.setenv("MINIAGENT_MODEL_THINKING_LEVEL", "medium")
    JsonConfigLoader.get_instance().reload()
    mc = get_default_model_config()
    assert mc.thinking_level == "medium"
    assert mc.thinking_budget == 8192


def test_openai_thinking_budget_overrides_derived_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """MINIAGENT_MODEL_THINKING_BUDGET 可覆盖自动计算的预算。"""
    monkeypatch.setenv("MINIAGENT_MODEL_THINKING_LEVEL", "high")
    monkeypatch.setenv("MINIAGENT_MODEL_THINKING_BUDGET", "12345")
    monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
    JsonConfigLoader.get_instance().reload()
    mc = get_default_model_config()
    assert mc.thinking_level == "heavy"
    assert mc.thinking_budget == 12345


def test_env_context_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """MINIAGENT_MODEL_CONTEXT_WINDOW 和 MINIAGENT_MODEL_MAX_TOKENS 覆盖。"""
    monkeypatch.delenv("MINIAGENT_MODEL_THINKING_BUDGET", raising=False)
    monkeypatch.delenv("MINIAGENT_MODEL_THINKING_LEVEL", raising=False)
    monkeypatch.delenv("MINIAGENT_MODEL_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("MINIAGENT_MODEL_MAX_TOKENS", raising=False)
    monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
    JsonConfigLoader.get_instance().reload()
    mc = get_default_model_config()
    assert mc.context_window == 128000
    assert mc.max_tokens == 4096

    monkeypatch.setenv("MINIAGENT_MODEL_CONTEXT_WINDOW", "40000")
    monkeypatch.setenv("MINIAGENT_MODEL_MAX_TOKENS", "9000")
    JsonConfigLoader.get_instance().reload()
    mc2 = get_default_model_config()
    assert mc2.context_window == 40000
    assert mc2.max_tokens == 9000