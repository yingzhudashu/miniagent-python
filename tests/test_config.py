"""Tests for miniagent.core.config — 模型与 Agent 配置管理（双 JSON）。"""

import json
import pathlib

import pytest

from miniagent.core.config import (
    get_default_agent_config,
    get_default_model_config,
    merge_agent_config,
)
from miniagent.infrastructure.json_config import JsonConfigLoader, get_config, get_config_section

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DEFAULTS_PATH = str(PROJECT_ROOT / "config.defaults.json")


def _install_loader(tmp_path: pathlib.Path, user_overrides: dict | None = None) -> str:
    user_path = tmp_path / "config.user.json"
    if user_overrides:
        user_path.write_text(json.dumps(user_overrides), encoding="utf-8")
    else:
        user_path.write_text("{}", encoding="utf-8")
    JsonConfigLoader._instance = JsonConfigLoader(
        defaults_path=DEFAULTS_PATH,
        user_path=str(user_path),
    )
    return str(user_path)


class TestGetDefaultModelConfig:
    def test_defaults(self, tmp_path):
        _install_loader(tmp_path)
        cfg = get_default_model_config()
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.max_tokens == 4096
        assert cfg.context_window == 128000
        assert cfg.temperature == 0.7
        assert cfg.top_p == 1.0
        assert cfg.thinking_level == "light"
        assert cfg.thinking_budget == 1024
        assert cfg.retry_count == 2

    def test_user_json_override(self, tmp_path):
        _install_loader(tmp_path, {"model": {"model": "gpt-4", "temperature": 0.5}})
        cfg = get_default_model_config()
        assert cfg.model == "gpt-4"
        assert cfg.temperature == 0.5

    def test_env_ignored(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        _install_loader(tmp_path)
        monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-4o")
        monkeypatch.setenv("MINIAGENT_MODEL_TEMPERATURE", "0.1")
        JsonConfigLoader.get_instance().reload()
        cfg = get_default_model_config()
        assert cfg.model == "gpt-4o-mini"
        assert cfg.temperature == 0.7

    def test_thinking_levels(self, tmp_path):
        for input_level, expected_level, expected_budget in [
            ("low", "light", 1024),
            ("medium", "medium", 8192),
            ("high", "heavy", 81920),
        ]:
            _install_loader(tmp_path, {"model": {"thinking_level": input_level}})
            cfg = get_default_model_config()
            assert cfg.thinking_level == expected_level
            assert cfg.thinking_budget == expected_budget

    def test_thinking_budget_override(self, tmp_path):
        _install_loader(
            tmp_path,
            {"model": {"thinking_level": "light", "thinking_budget": 4096}},
        )
        cfg = get_default_model_config()
        assert cfg.thinking_level == "light"
        assert cfg.thinking_budget == 4096


class TestGetDefaultAgentConfig:
    def test_defaults(self, tmp_path):
        _install_loader(tmp_path)
        cfg = get_default_agent_config()
        assert cfg.max_turns == 400
        assert cfg.tool_timeout == 60
        assert cfg.http_timeout == 120
        assert cfg.debug is False
        assert cfg.history_progressive_compression is True

    def test_user_json_override(self, tmp_path):
        _install_loader(tmp_path, {"agent": {"max_turns": 100, "debug": True}})
        cfg = get_default_agent_config()
        assert cfg.max_turns == 100
        assert cfg.debug is True

    def test_loop_detection_copy(self, tmp_path):
        _install_loader(tmp_path)
        cfg = get_default_agent_config()
        agent_section = get_config_section("agent")
        default_loop_detection = agent_section.get("loop_detection", {})
        assert cfg.loop_detection is not default_loop_detection
        assert cfg.loop_detection == default_loop_detection


class TestJsonConfigLoader:
    def test_metadata_keys_filtered(self, tmp_path):
        _install_loader(tmp_path)
        assert get_config("_config_guide.usage") is None
        sections = JsonConfigLoader.get_instance()._defaults
        assert "_config_guide" not in sections

    def test_user_overrides_defaults(self, tmp_path):
        _install_loader(tmp_path, {"paths": {"state_dir": "custom-ws"}})
        assert get_config("paths.state_dir") == "custom-ws"


class TestMergeAgentConfig:
    def test_override_single_field(self, tmp_path):
        _install_loader(tmp_path)
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"max_turns": 50})
        assert merged.max_turns == 50
        assert merged.tool_timeout == base.tool_timeout
