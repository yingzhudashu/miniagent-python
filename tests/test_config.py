"""Agent configuration reads only immutable injected settings."""

from miniagent.agent.config import get_default_agent_config, merge_agent_config
from miniagent.agent.settings import AgentSettings, use_agent_settings


def test_default_agent_config_reads_injected_snapshot() -> None:
    settings = AgentSettings(
        {
            "agent": {
                "max_turns": 12,
                "tool_timeout": 8,
                "allow_parallel_tools": False,
                "debug": True,
            },
            "memory": {"history_progressive": False},
        }
    )
    with use_agent_settings(settings):
        config = get_default_agent_config()
    assert config.max_turns == 12
    assert config.tool_timeout == 8
    assert config.allow_parallel_tools is False
    assert config.debug is True
    assert config.history_progressive_compression is False


def test_agent_settings_are_immutable() -> None:
    source = {"agent": {"max_turns": 3}}
    settings = AgentSettings(source)
    source["agent"]["max_turns"] = 99
    assert settings.get_path("agent.max_turns") == 3


def test_merge_agent_config_uses_llm_overrides_only() -> None:
    base = get_default_agent_config()
    merged = merge_agent_config(base, {"llm_overrides": {"profile": "fast"}})
    assert merged.llm_overrides == {"profile": "fast"}
