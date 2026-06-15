"""miniagent.types.config — 配置类型与 normalize / 分组同步回归。"""

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
    def test_wrapped_dict(self) -> None:
        raw = {"session_id": "default", "messages": [{"role": "user", "content": "hi"}]}
        assert normalize_conversation_history(raw) == [{"role": "user", "content": "hi"}]

    def test_filters_invalid_entries(self) -> None:
        raw = [{"role": "user", "content": "a"}, "skip", {"role": "assistant", "content": "b"}]
        assert len(normalize_conversation_history(raw)) == 2

    def test_none_and_empty(self) -> None:
        assert normalize_conversation_history(None) == []
        assert normalize_conversation_history([]) == []

    def test_dict_without_messages(self) -> None:
        assert normalize_conversation_history({"session_id": "x"}) == []

    def test_missing_or_invalid_role(self) -> None:
        assert normalize_conversation_history([{"content": "x"}]) == []
        assert normalize_conversation_history([{"role": 1, "content": "x"}]) == []

    def test_empty_role_string_kept(self) -> None:
        assert normalize_conversation_history([{"role": "", "content": "x"}]) == [
            {"role": "", "content": "x"}
        ]


class TestAgentConfigPostInit:
    def test_session_config_syncs_to_flat(self) -> None:
        cfg = AgentConfig(session_config=SessionBindingConfig(session_key="foo"))
        assert cfg.session_key == "foo"
        assert cfg.session_config is not None
        assert cfg.session_config.session_key == "foo"

    def test_flat_session_fields_build_session_config(self) -> None:
        cfg = AgentConfig(session_key="bar", session_workspace="/ws/bar")
        assert cfg.session_config is not None
        assert cfg.session_config.session_key == "bar"
        assert cfg.session_config.session_workspace == "/ws/bar"

    def test_session_config_wins_over_conflicting_flat(self) -> None:
        cfg = AgentConfig(
            session_config=SessionBindingConfig(session_key="grouped"),
            session_key="flat",
        )
        assert cfg.session_key == "grouped"

    def test_feishu_config_syncs_to_flat(self) -> None:
        cfg = AgentConfig(feishu_config=FeishuChannelConfig(receive_chat_id="oc_x"))
        assert cfg.feishu_receive_chat_id == "oc_x"
        assert cfg.feishu_config is not None
        assert cfg.feishu_config.receive_chat_id == "oc_x"

    def test_flat_feishu_fields_build_feishu_config(self) -> None:
        cfg = AgentConfig(
            feishu_receive_chat_id="oc_y",
            feishu_trigger_message_id="om_1",
        )
        assert cfg.feishu_config is not None
        assert cfg.feishu_config.receive_chat_id == "oc_y"
        assert cfg.feishu_config.trigger_message_id == "om_1"

    def test_feishu_config_wins_over_conflicting_flat(self) -> None:
        cfg = AgentConfig(
            feishu_config=FeishuChannelConfig(receive_chat_id="oc_grouped"),
            feishu_receive_chat_id="oc_flat",
        )
        assert cfg.feishu_receive_chat_id == "oc_grouped"

    def test_cli_dispatch_false_builds_feishu_config(self) -> None:
        cfg = AgentConfig(cli_dispatch_allow_mutations=False)
        assert cfg.feishu_config is not None
        assert cfg.feishu_config.cli_dispatch_allow_mutations is False

    def test_default_agent_has_no_feishu_config(self) -> None:
        cfg = AgentConfig()
        assert cfg.feishu_config is None


class TestMergeAgentConfigRegression:
    def test_merge_preserves_session_registry(self) -> None:
        reg = DefaultToolRegistry()
        base = get_default_agent_config()
        base = merge_agent_config(base, {"session_registry": reg})
        merged = merge_agent_config(base, {"max_turns": 3})
        assert merged.session_registry is reg
        assert merged.max_turns == 3

    def test_merge_allows_session_registry_override(self) -> None:
        r1 = DefaultToolRegistry()
        r2 = DefaultToolRegistry()
        base = merge_agent_config(get_default_agent_config(), {"session_registry": r1})
        merged = merge_agent_config(base, {"session_registry": r2})
        assert merged.session_registry is r2

    def test_merge_preserves_risk_level(self) -> None:
        base = merge_agent_config(get_default_agent_config(), {"risk_level": "high"})
        merged = merge_agent_config(base, {"debug": True})
        assert merged.risk_level == "high"
        assert merged.debug is True


class TestMergeAgentConfigGrouped:
    def test_session_config_dict_syncs_flat_fields(self) -> None:
        merged = merge_agent_config(
            get_default_agent_config(),
            {"session_config": {"session_key": "sk-1", "session_workspace": "/ws/sk-1"}},
        )
        assert merged.session_key == "sk-1"
        assert merged.session_workspace == "/ws/sk-1"
        assert merged.session_config is not None
        assert merged.session_config.session_key == "sk-1"

    def test_feishu_config_dict_syncs_flat_fields(self) -> None:
        merged = merge_agent_config(
            get_default_agent_config(),
            {
                "feishu_config": {
                    "receive_chat_id": "oc_abc",
                    "trigger_message_id": "om_xyz",
                    "cli_dispatch_allow_mutations": False,
                }
            },
        )
        assert merged.feishu_receive_chat_id == "oc_abc"
        assert merged.feishu_trigger_message_id == "om_xyz"
        assert merged.cli_dispatch_allow_mutations is False
        assert merged.feishu_config is not None
        assert merged.feishu_config.receive_chat_id == "oc_abc"

    def test_grouped_and_flat_merge_preserves_session_registry(self) -> None:
        reg = DefaultToolRegistry()
        base = merge_agent_config(get_default_agent_config(), {"session_registry": reg})
        merged = merge_agent_config(
            base,
            {"session_config": {"session_key": "with-reg"}},
        )
        assert merged.session_key == "with-reg"
        assert merged.session_registry is reg
        assert merged.session_config is not None
        assert merged.session_config.session_registry is reg

    def test_grouped_wins_over_conflicting_flat_session_key(self) -> None:
        merged = merge_agent_config(
            get_default_agent_config(),
            {
                "session_config": {"session_key": "grouped"},
                "session_key": "flat",
            },
        )
        assert merged.session_key == "grouped"
        assert merged.session_config is not None
        assert merged.session_config.session_key == "grouped"

    def test_grouped_wins_over_conflicting_flat_feishu_key(self) -> None:
        merged = merge_agent_config(
            get_default_agent_config(),
            {
                "feishu_config": {"receive_chat_id": "oc_grouped"},
                "feishu_receive_chat_id": "oc_flat",
            },
        )
        assert merged.feishu_receive_chat_id == "oc_grouped"
        assert merged.feishu_config is not None
        assert merged.feishu_config.receive_chat_id == "oc_grouped"

    def test_empty_session_config_dict_preserves_base(self) -> None:
        base = merge_agent_config(
            get_default_agent_config(),
            {"session_key": "existing"},
        )
        merged = merge_agent_config(base, {"session_config": {}})
        assert merged.session_key == "existing"
        assert merged.session_config is not None
        assert merged.session_config.session_key == "existing"

    def test_empty_feishu_config_dict_preserves_base(self) -> None:
        base = merge_agent_config(
            get_default_agent_config(),
            {"feishu_receive_chat_id": "oc_existing"},
        )
        merged = merge_agent_config(base, {"feishu_config": {}})
        assert merged.feishu_receive_chat_id == "oc_existing"
        assert merged.feishu_config is not None
        assert merged.feishu_config.receive_chat_id == "oc_existing"
