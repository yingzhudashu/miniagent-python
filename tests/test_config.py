"""Tests for miniagent.core.config — 模型与 Agent 配置管理。"""

import pytest

from miniagent.core.config import (
    DEFAULT_LOOP_DETECTION,
    get_default_agent_config,
    get_default_model_config,
    merge_agent_config,
)


class TestGetDefaultModelConfig:
    """get_default_model_config 从环境变量读取并返回 ModelConfig。"""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch):
        """清空所有相关 env 时返回默认值。"""
        for key in [
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "AGENT_CONTEXT_WINDOW",
            "OPENAI_MAX_TOKENS",
            "AGENT_THINKING_DEFAULT",
            "OPENAI_THINKING_BUDGET",
            "AGENT_TEMPERATURE",
            "AGENT_TOP_P",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = get_default_model_config()
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.max_tokens == 4096
        assert cfg.context_window == 128000
        assert cfg.temperature == 0.7
        assert cfg.top_p == 1.0
        assert cfg.thinking_level == "light"
        assert cfg.thinking_budget == 1024
        assert cfg.stream is False
        assert cfg.retry_count == 2

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
        monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
        monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
        cfg = get_default_model_config()
        assert cfg.model == "gpt-4"

    def test_thinking_levels(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
        for level, expected in [
            ("low", ("light", 1024)),
            ("medium", ("medium", 8192)),
            ("high", ("heavy", 81920)),
        ]:
            monkeypatch.setenv("AGENT_THINKING_DEFAULT", level)
            cfg = get_default_model_config()
            assert cfg.thinking_level == expected[0], f"Failed for {level}"
            assert cfg.thinking_budget == expected[1], f"Failed for {level}"

    def test_thinking_budget_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_THINKING_DEFAULT", "low")
        monkeypatch.setenv("OPENAI_THINKING_BUDGET", "4096")
        cfg = get_default_model_config()
        assert cfg.thinking_level == "light"
        assert cfg.thinking_budget == 4096

    def test_context_window_and_max_tokens(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_CONTEXT_WINDOW", "64000")
        monkeypatch.setenv("OPENAI_MAX_TOKENS", "2048")
        monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
        monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
        cfg = get_default_model_config()
        assert cfg.context_window == 64000
        assert cfg.max_tokens == 2048

    def test_base_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
        monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
        monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
        cfg = get_default_model_config()
        assert cfg.base_url == "https://api.example.com/v1"


class TestGetDefaultAgentConfig:
    """get_default_agent_config 从环境变量读取并返回 AgentConfig。"""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch):
        for key in [
            "AGENT_MAX_TURNS",
            "AGENT_TOOL_TIMEOUT",
            "AGENT_HTTP_TIMEOUT",
            "AGENT_CONTEXT_RESERVE",
            "AGENT_CONTEXT_COMPRESS_THRESHOLD",
            "AGENT_DEBUG",
            "AGENT_LOG_TOKEN_USAGE",
            "MINI_AGENT_HISTORY_PROGRESSIVE",
        ]:
            monkeypatch.delenv(key, raising=False)

        cfg = get_default_agent_config()
        assert cfg.max_turns == 400
        assert cfg.tool_timeout == 60
        assert cfg.http_timeout == 120
        assert cfg.context_reserve_ratio == 0.15
        assert cfg.context_compress_threshold == 0.6
        assert cfg.debug is False
        assert cfg.log_token_usage is True
        assert cfg.history_progressive_compression is True
        assert cfg.compress_messages is True
        assert cfg.allow_parallel_tools is True

    def test_custom_max_turns(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_MAX_TURNS", "100")
        cfg = get_default_agent_config()
        assert cfg.max_turns == 100

    def test_debug_mode(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AGENT_DEBUG", "true")
        cfg = get_default_agent_config()
        assert cfg.debug is True

    def test_loop_detection_copy(self, monkeypatch: pytest.MonkeyPatch):
        cfg = get_default_agent_config()
        assert cfg.loop_detection is not DEFAULT_LOOP_DETECTION
        assert cfg.loop_detection == DEFAULT_LOOP_DETECTION


class TestMergeAgentConfig:
    """merge_agent_config 合并覆盖配置到基础配置。"""

    def test_override_single_field(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"max_turns": 50})
        assert merged.max_turns == 50
        # 其它字段不变
        assert merged.tool_timeout == base.tool_timeout

    def test_override_multiple_fields(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"max_turns": 100, "tool_timeout": 120})
        assert merged.max_turns == 100
        assert merged.tool_timeout == 120

    def test_loop_detection_merge(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"loop_detection": {"enabled": False}})
        assert merged.loop_detection["enabled"] is False
        # 未覆盖的子字段保留
        assert merged.loop_detection["history_size"] == base.loop_detection["history_size"]

    def test_unknown_key_ignored(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"nonexistent_key": 42})
        assert merged.max_turns == base.max_turns

    def test_model_overrides_merge(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"model_overrides": {"thinking_level": "heavy"}})
        assert merged.model_overrides["thinking_level"] == "heavy"

    def test_conversation_history_normalized(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"conversation_history": [{"role": "user", "content": "hi"}]})
        assert len(merged.conversation_history) == 1

    def test_empty_overrides_returns_same_values(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {})
        assert merged.max_turns == base.max_turns
        assert merged.tool_timeout == base.tool_timeout
