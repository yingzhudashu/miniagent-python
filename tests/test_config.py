"""Tests for miniagent.core.config — 模型与 Agent 配置管理。

环境变量命名规则：MINIAGENT_<SECTION_KEY>（如 MINIAGENT_MODEL_MODEL）
"""

import pathlib

import pytest

from miniagent.core.config import (
    get_default_agent_config,
    get_default_model_config,
    merge_agent_config,
)
from miniagent.infrastructure.json_config import JsonConfigLoader, get_config_section

# 项目根目录（config.defaults.json所在位置）
PROJECT_ROOT = pathlib.Path(__file__).parent.parent


class TestGetDefaultModelConfig:
    """get_default_model_config 从JSON配置读取并返回 ModelConfig。"""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """清空所有相关 env 时返回JSON默认值。"""
        # 清空可能影响测试的环境变量
        for key in [
            "MINIAGENT_MODEL_BASE_URL",
            "MINIAGENT_MODEL_MODEL",
            "MINIAGENT_MODEL_CONTEXT_WINDOW",
            "MINIAGENT_MODEL_MAX_TOKENS",
            "MINIAGENT_MODEL_THINKING_LEVEL",
            "MINIAGENT_MODEL_THINKING_BUDGET",
            "MINIAGENT_MODEL_TEMPERATURE",
            "MINIAGENT_MODEL_TOP_P",
            "MINIAGENT_CONFIG",
        ]:
            monkeypatch.delenv(key, raising=False)

        # 使用临时目录作为用户配置路径（避免加载config.user.json）
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )

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

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """单项环境变量覆盖模型名称。"""
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_LEVEL", raising=False)
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_BUDGET", raising=False)
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
        monkeypatch.setenv("MINIAGENT_MODEL_MODEL", "gpt-4")

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_model_config()
        assert cfg.model == "gpt-4"

    def test_json_config_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """MINIAGENT_CONFIG环境变量（JSON格式）覆盖配置。"""
        import json
        config_json = json.dumps({"model": {"model": "gpt-4o", "temperature": 0.5}})
        monkeypatch.setenv("MINIAGENT_CONFIG", config_json)
        monkeypatch.delenv("MINIAGENT_MODEL_MODEL", raising=False)
        monkeypatch.delenv("MINIAGENT_MODEL_TEMPERATURE", raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_model_config()
        assert cfg.model == "gpt-4o"
        assert cfg.temperature == 0.5

    def test_single_env_overrides_json_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """单项环境变量优先级高于MINIAGENT_CONFIG。"""
        import json
        config_json = json.dumps({"model": {"temperature": 0.5}})
        monkeypatch.setenv("MINIAGENT_CONFIG", config_json)
        monkeypatch.setenv("MINIAGENT_MODEL_TEMPERATURE", "0.9")

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_model_config()
        assert cfg.temperature == 0.9

    def test_thinking_levels(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """thinking_level映射到对应的thinking_budget。"""
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_BUDGET", raising=False)
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        for level, expected in [
            ("light", ("light", 1024)),  # JSON默认值
            ("medium", ("medium", 8192)),
            ("heavy", ("heavy", 81920)),
        ]:
            # 设置thinking_level（使用原始档位名low/medium/high或映射后的名称）
            input_level = {"light": "low", "medium": "medium", "heavy": "high"}.get(expected[0], expected[0])
            monkeypatch.setenv("MINIAGENT_MODEL_THINKING_LEVEL", input_level)
            JsonConfigLoader.get_instance().reload()
            cfg = get_default_model_config()
            assert cfg.thinking_level == expected[0], f"Failed for {level}"
            assert cfg.thinking_budget == expected[1], f"Failed for {level}"

    def test_thinking_budget_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("MINIAGENT_MODEL_THINKING_LEVEL", "light")
        monkeypatch.setenv("MINIAGENT_MODEL_THINKING_BUDGET", "4096")
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_model_config()
        assert cfg.thinking_level == "light"
        assert cfg.thinking_budget == 4096

    def test_context_window_and_max_tokens(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("MINIAGENT_MODEL_CONTEXT_WINDOW", "64000")
        monkeypatch.setenv("MINIAGENT_MODEL_MAX_TOKENS", "2048")
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_LEVEL", raising=False)
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_BUDGET", raising=False)
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_model_config()
        assert cfg.context_window == 64000
        assert cfg.max_tokens == 2048

    def test_base_url(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("MINIAGENT_MODEL_BASE_URL", "https://api.example.com/v1")
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_LEVEL", raising=False)
        monkeypatch.delenv("MINIAGENT_MODEL_THINKING_BUDGET", raising=False)
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_model_config()
        assert cfg.base_url == "https://api.example.com/v1"


class TestGetDefaultAgentConfig:
    """get_default_agent_config 从JSON配置读取并返回 AgentConfig。"""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        for key in [
            "MINIAGENT_AGENT_MAX_TURNS",
            "MINIAGENT_AGENT_TOOL_TIMEOUT",
            "MINIAGENT_AGENT_HTTP_TIMEOUT",
            "MINIAGENT_AGENT_CONTEXT_RESERVE_RATIO",
            "MINIAGENT_AGENT_CONTEXT_COMPRESS_THRESHOLD",
            "MINIAGENT_AGENT_DEBUG",
            "MINIAGENT_AGENT_LOG_TOKEN_USAGE",
            "MINIAGENT_MEMORY_HISTORY_PROGRESSIVE",
            "MINIAGENT_CONFIG",
        ]:
            monkeypatch.delenv(key, raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
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

    def test_custom_max_turns(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("MINIAGENT_AGENT_MAX_TURNS", "100")
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_agent_config()
        assert cfg.max_turns == 100

    def test_debug_mode(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("MINIAGENT_AGENT_DEBUG", "true")
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)

        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        cfg = get_default_agent_config()
        assert cfg.debug is True

    def test_loop_detection_copy(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )

        cfg = get_default_agent_config()
        # loop_detection应该从JSON配置加载
        agent_section = get_config_section("agent")
        default_loop_detection = agent_section.get("loop_detection", {})
        assert cfg.loop_detection is not default_loop_detection  # 应该是副本
        assert cfg.loop_detection == default_loop_detection  # 值应该相等


class TestMergeAgentConfig:
    """merge_agent_config 合并覆盖配置到基础配置。"""

    def test_override_single_field(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"max_turns": 50})
        assert merged.max_turns == 50
        # 其它字段不变
        assert merged.tool_timeout == base.tool_timeout

    def test_override_multiple_fields(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"max_turns": 100, "tool_timeout": 120})
        assert merged.max_turns == 100
        assert merged.tool_timeout == 120

    def test_loop_detection_merge(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"loop_detection": {"enabled": False}})
        assert merged.loop_detection["enabled"] is False
        # 未覆盖的子字段保留
        assert merged.loop_detection["history_size"] == base.loop_detection["history_size"]

    def test_unknown_key_ignored(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"nonexistent_key": 42})
        assert merged.max_turns == base.max_turns

    def test_model_overrides_merge(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"model_overrides": {"thinking_level": "heavy"}})
        assert merged.model_overrides["thinking_level"] == "heavy"

    def test_conversation_history_normalized(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"conversation_history": [{"role": "user", "content": "hi"}]})
        assert len(merged.conversation_history) == 1

    def test_empty_overrides_returns_same_values(self, tmp_path):
        empty_user_path = str(tmp_path / "config.user.json")
        JsonConfigLoader._instance = JsonConfigLoader(
            defaults_path=str(PROJECT_ROOT / "config.defaults.json"),
            user_path=empty_user_path
        )
        base = get_default_agent_config()
        merged = merge_agent_config(base, {})
        assert merged.max_turns == base.max_turns
        assert merged.tool_timeout == base.tool_timeout