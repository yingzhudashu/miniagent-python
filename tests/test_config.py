"""Tests for miniagent.core.config — 模型与 Agent 配置管理（双 JSON）。"""

import json
import pathlib

import pytest

from miniagent.core.config import (
    get_default_agent_config,
    get_default_model_config,
    merge_agent_config,
)
from miniagent.infrastructure.json_config import (
    JsonConfigLoader,
    get_config,
    get_config_section,
    install_config_loader,
    reload_config,
)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
from miniagent.infrastructure.json_config import _packaged_defaults_path

DEFAULTS_PATH = _packaged_defaults_path()


def _install_loader(tmp_path: pathlib.Path, user_overrides: dict | None = None) -> str:
    user_path = tmp_path / "config.user.json"
    if user_overrides:
        user_path.write_text(json.dumps(user_overrides), encoding="utf-8")
    else:
        user_path.write_text("{}", encoding="utf-8")
    install_config_loader(
        JsonConfigLoader(defaults_path=DEFAULTS_PATH, user_path=str(user_path))
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
        assert cfg.wire_api == "chat_completions"
        assert cfg.user_agent is None

    def test_user_json_override(self, tmp_path):
        _install_loader(
            tmp_path,
            {
                "model": {
                    "model": "gpt-4",
                    "temperature": 0.5,
                    "wire_api": "responses",
                    "user_agent": "MiniAgent-Test",
                }
            },
        )
        cfg = get_default_model_config()
        assert cfg.model == "gpt-4"
        assert cfg.temperature == 0.5
        assert cfg.wire_api == "responses"
        assert cfg.user_agent == "MiniAgent-Test"

    def test_invalid_wire_api_rejected(self, tmp_path):
        _install_loader(tmp_path, {"model": {"wire_api": "legacy"}})
        with pytest.raises(ValueError, match="model.wire_api"):
            get_default_model_config()

    def test_env_ignored(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        _install_loader(tmp_path)
        monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-4o")
        monkeypatch.setenv("MINIAGENT_MODEL_TEMPERATURE", "0.1")
        reload_config()
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

    def test_cfg_bool_string_false(self, tmp_path):
        _install_loader(tmp_path, {"agent": {"debug": "false", "allow_parallel_tools": "false"}})
        cfg = get_default_agent_config()
        assert cfg.debug is False
        assert cfg.allow_parallel_tools is False

    def test_cfg_bool_string_true(self, tmp_path):
        _install_loader(tmp_path, {"agent": {"debug": "true"}})
        cfg = get_default_agent_config()
        assert cfg.debug is True

    def test_hardcoded_fields_not_from_json(self, tmp_path):
        _install_loader(
            tmp_path,
            {
                "agent": {
                    "context_overflow_strategy": "truncate",
                    "compress_messages": False,
                    "tool_selection_strategy": "all",
                    "auto_execute_confirmed": True,
                    "response_language": "en-US",
                    "response_format": "text",
                    "log_file": "/tmp/agent.log",
                }
            },
        )
        cfg = get_default_agent_config()
        assert cfg.context_overflow_strategy == "summarize"
        assert cfg.compress_messages is True
        assert cfg.tool_selection_strategy == "toolbox"
        assert cfg.auto_execute_confirmed is False
        assert cfg.response_language == "zh-CN"
        assert cfg.response_format == "markdown"
        assert cfg.log_file is None


class TestJsonConfigLoader:
    def test_metadata_keys_filtered(self, tmp_path):
        _install_loader(tmp_path)
        assert get_config("_config_guide.usage") is None

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

    def test_loop_detection_partial_merge(self, tmp_path):
        _install_loader(tmp_path)
        base = get_default_agent_config()
        assert base.loop_detection.get("enabled") is True
        merged = merge_agent_config(base, {"loop_detection": {"enabled": False, "warning_threshold": 99}})
        assert merged.loop_detection["enabled"] is False
        assert merged.loop_detection["warning_threshold"] == 99
        assert merged.loop_detection.get("history_size") == base.loop_detection.get("history_size")

    def test_unknown_override_key_ignored(self, tmp_path):
        from unittest.mock import patch

        _install_loader(tmp_path)
        base = get_default_agent_config()
        with patch("miniagent.core.config._logger.debug") as mock_debug:
            merged = merge_agent_config(base, {"unknown_field": 1, "max_turns": 10})
        assert merged.max_turns == 10
        mock_debug.assert_any_call("merge_agent_config: 忽略未知覆盖键 %r", "unknown_field")

    def test_model_overrides_partial_merge(self, tmp_path):
        _install_loader(tmp_path)
        base = merge_agent_config(
            get_default_agent_config(),
            {"model_overrides": {"model": "gpt-base", "temperature": 0.2}},
        )
        merged = merge_agent_config(base, {"model_overrides": {"temperature": 0.9, "max_tokens": 256}})
        assert merged.model_overrides["model"] == "gpt-base"
        assert merged.model_overrides["temperature"] == 0.9
        assert merged.model_overrides["max_tokens"] == 256

    def test_merge_can_override_hardcoded_defaults(self, tmp_path):
        _install_loader(tmp_path)
        base = get_default_agent_config()
        assert base.response_language == "zh-CN"
        merged = merge_agent_config(
            base,
            {
                "response_language": "en-US",
                "tool_selection_strategy": "all",
                "context_overflow_strategy": "truncate",
            },
        )
        assert merged.response_language == "en-US"
        assert merged.tool_selection_strategy == "all"
        assert merged.context_overflow_strategy == "truncate"
