"""Tests for grouped Agent configuration and history normalization."""

from __future__ import annotations

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.config import (
    AgentConfig,
    FeishuChannelConfig,
    SessionBindingConfig,
    normalize_conversation_history,
)


class TestNormalizeConversationHistory:
    def test_filters_invalid_entries(self) -> None:
        raw = [
            {"role": "user", "content": "a"},
            "skip",
            {"role": "assistant", "content": "b"},
        ]
        assert len(normalize_conversation_history(raw)) == 2

    def test_non_list_and_empty(self) -> None:
        assert normalize_conversation_history(None) == []
        assert normalize_conversation_history({"messages": []}) == []
        assert normalize_conversation_history([]) == []

    def test_missing_or_invalid_role(self) -> None:
        assert normalize_conversation_history([{"content": "x"}]) == []
        assert normalize_conversation_history([{"role": 1, "content": "x"}]) == []

    def test_empty_role_string_kept(self) -> None:
        assert normalize_conversation_history([{"role": "", "content": "x"}]) == [
            {"role": "", "content": "x"}
        ]


class TestAgentConfigGroups:
    def test_explicit_session_group(self) -> None:
        cfg = AgentConfig(
            session_config=SessionBindingConfig(
                session_key="foo",
                session_workspace="/ws/foo",
            )
        )
        assert cfg.session_config.session_key == "foo"
        assert cfg.session_config.session_workspace == "/ws/foo"

    def test_explicit_feishu_group(self) -> None:
        cfg = AgentConfig(
            feishu_config=FeishuChannelConfig(
                receive_chat_id="oc_x",
                cli_dispatch_allow_mutations=False,
            )
        )
        assert cfg.feishu_config.receive_chat_id == "oc_x"
        assert cfg.feishu_config.cli_dispatch_allow_mutations is False

    def test_default_groups_are_available(self) -> None:
        cfg = AgentConfig()
        assert cfg.session_config == SessionBindingConfig()
        assert cfg.feishu_config == FeishuChannelConfig()


class TestMergeAgentConfig:
    def test_merge_preserves_and_overrides_session_registry(self) -> None:
        first = DefaultToolRegistry()
        second = DefaultToolRegistry()
        base = merge_agent_config(
            get_default_agent_config(),
            {"session_config": {"session_registry": first}},
        )
        preserved = merge_agent_config(base, {"max_turns": 3})
        assert preserved.session_config.session_registry is first
        assert preserved.max_turns == 3

        replaced = merge_agent_config(
            preserved,
            {"session_config": {"session_registry": second}},
        )
        assert replaced.session_config.session_registry is second

    def test_merge_preserves_risk_level(self) -> None:
        base = merge_agent_config(get_default_agent_config(), {"risk_level": "high"})
        merged = merge_agent_config(base, {"debug": True})
        assert merged.risk_level == "high"
        assert merged.debug is True

    def test_session_group_is_merged_field_by_field(self) -> None:
        registry = DefaultToolRegistry()
        base = merge_agent_config(
            get_default_agent_config(),
            {
                "session_config": {
                    "session_key": "before",
                    "session_registry": registry,
                }
            },
        )
        merged = merge_agent_config(
            base,
            {"session_config": {"session_key": "after"}},
        )
        assert merged.session_config.session_key == "after"
        assert merged.session_config.session_registry is registry

    def test_feishu_group_is_merged_field_by_field(self) -> None:
        base = merge_agent_config(
            get_default_agent_config(),
            {"feishu_config": {"receive_chat_id": "oc_abc"}},
        )
        merged = merge_agent_config(
            base,
            {"feishu_config": {"trigger_message_id": "om_xyz"}},
        )
        assert merged.feishu_config.receive_chat_id == "oc_abc"
        assert merged.feishu_config.trigger_message_id == "om_xyz"

    def test_history_is_normalized_inside_session_group(self) -> None:
        merged = merge_agent_config(
            get_default_agent_config(),
            {
                "session_config": {
                    "conversation_history": [
                        {"role": "user", "content": "ok"},
                        "invalid",
                    ]
                }
            },
        )
        assert merged.session_config.conversation_history == [
            {"role": "user", "content": "ok"}
        ]
