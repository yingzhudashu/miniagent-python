"""外部 MINIAGENT_CONFIG 加载（无密钥样例）。"""

import json
import os
from pathlib import Path

import pytest

from miniagent.core.openai_client import reset_shared_async_openai_for_tests
from miniagent.runtime import external_config as ec


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    reset_shared_async_openai_for_tests()
    ec.reset_external_config_for_tests()
    yield
    ec.reset_external_config_for_tests()
    reset_shared_async_openai_for_tests()


def test_load_external_config_sets_env_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)

    cfg = {
        "models": {
            "providers": {
                "bailian": {
                    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "apiKey": "test-key-placeholder",
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": "bailian/qwen-test"},
                "contextTokens": 32000,
                "thinkingDefault": "medium",
                "models": {
                    "bailian/qwen-test": {"params": {"thinking_budget": 4096}},
                },
            }
        },
    }
    p = tmp_path / "miniagent_test.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MINIAGENT_CONFIG", str(p))

    patch = ec.load_external_config_from_env()
    assert patch.base_url is not None
    assert os.environ.get("OPENAI_BASE_URL") == patch.base_url
    assert os.environ.get("OPENAI_MODEL") == "qwen-test"
    assert os.environ.get("OPENAI_API_KEY") == "test-key-placeholder"
    assert os.environ.get("AGENT_CONTEXT_WINDOW") == "32000"

    ep = ec.get_external_config_patch()
    assert ep.get("thinking_default") == "medium"
    assert ep["thinking_budget_by_model"].get("qwen-test") == 4096


def test_missing_config_file_clears_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CONFIG", "/nonexistent/miniagent_x.json")
    ec.load_external_config_from_env()
    assert ec.get_external_config_patch() == {}


def test_provider_models_build_model_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    cfg = {
        "models": {
            "providers": {
                "bailian": {
                    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "models": [
                        {"id": "qwen-m", "contextWindow": 128000, "maxTokens": 6000},
                    ],
                }
            }
        },
        "agents": {"defaults": {"model": {"primary": "bailian/qwen-m"}}},
    }
    p = tmp_path / "limits.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MINIAGENT_CONFIG", str(p))
    ec.load_external_config_from_env()
    ep = ec.get_external_config_patch()
    assert ep["model_limits"]["qwen-m"]["context_window"] == 128000
    assert ep["model_limits"]["qwen-m"]["max_tokens"] == 6000


def test_bare_primary_resolves_provider_by_models_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = {
        "models": {
            "providers": {
                "p1": {
                    "baseUrl": "https://gw.example.com/v1",
                    "apiKey": "abc",
                    "models": [{"id": "solo-id"}],
                }
            }
        },
        "agents": {"defaults": {"model": {"primary": "solo-id"}}},
    }
    p = tmp_path / "bare.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MINIAGENT_CONFIG", str(p))
    ec.load_external_config_from_env()
    assert os.environ.get("OPENAI_BASE_URL") == "https://gw.example.com/v1"
    assert os.environ.get("OPENAI_MODEL") == "solo-id"


def test_model_limits_apply_in_get_default_model_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("OPENAI_MAX_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    cfg = {
        "models": {
            "providers": {
                "bailian": {
                    "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "models": [{"id": "qwen-z", "contextWindow": 40000, "maxTokens": 9000}],
                }
            }
        },
        "agents": {"defaults": {"model": {"primary": "bailian/qwen-z"}}},
    }
    p = tmp_path / "mc.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("MINIAGENT_CONFIG", str(p))
    ec.load_external_config_from_env()
    from miniagent.core.config import get_default_model_config

    mc = get_default_model_config()
    assert mc.model == "qwen-z"
    assert mc.context_window == 40000
    assert mc.max_tokens == 9000
