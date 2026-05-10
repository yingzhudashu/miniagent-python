"""AGENT_THINKING_DEFAULT / OPENAI_THINKING_BUDGET 与 get_default_model_config 合并。"""

import json
from pathlib import Path

import pytest

from miniagent.core.config import get_default_model_config
from miniagent.runtime import external_config as ec


@pytest.fixture(autouse=True)
def _reset_ext() -> None:
    ec.reset_external_config_for_tests()
    yield
    ec.reset_external_config_for_tests()


def test_agent_thinking_default_overrides_model_profile(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_env_thinking_beats_external_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = {
        "models": {
            "providers": {
                "bailian": {
                    "baseUrl": "https://example.com/v1",
                    "apiKey": "k",
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": "bailian/qwen-test"},
                "thinkingDefault": "low",
                "models": {
                    "bailian/qwen-test": {"params": {"thinking_budget": 4096}},
                },
            }
        },
    }
    p = tmp_path / "x.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MINIAGENT_CONFIG", str(p))
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
    monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
    ec.load_external_config_from_env()

    monkeypatch.setenv("AGENT_THINKING_DEFAULT", "high")
    mc = get_default_model_config()
    assert mc.model == "qwen-test"
    assert mc.thinking_level == "heavy"
    assert mc.thinking_budget == 81920

    monkeypatch.setenv("OPENAI_THINKING_BUDGET", "777")
    mc2 = get_default_model_config()
    assert mc2.thinking_budget == 777

    monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
    monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
    mc3 = get_default_model_config()
    assert mc3.thinking_level == "light"
    assert mc3.thinking_budget == 4096
