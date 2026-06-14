"""Tests for miniagent/engine/cli_state.py."""

from __future__ import annotations

from miniagent.engine.cli_state import CliLoopState
from tests.scheduled_tasks_helpers import minimal_cli_state, minimal_tick_ctx

_EXPECTED_REQUIRED_KEYS = frozenset(
    {
        "active_session_id",
        "skill_toolboxes",
        "skill_prompts",
        "feishu_enabled",
        "session_manager",
        "instance_id",
        "runtime_ctx",
        "feishu_p2p_synced_senders",
    }
)


def _forward_name(value: object) -> str:
    """Normalize annotation under ``from __future__ import annotations``."""
    if isinstance(value, str):
        return value
    forward = getattr(value, "__forward_arg__", None)
    if forward is not None:
        return forward
    return str(value)


class TestCliLoopStateShape:
    """CliLoopState keys align with runtime helpers and annotations."""

    def test_required_and_optional_keys(self):
        assert CliLoopState.__required_keys__ == _EXPECTED_REQUIRED_KEYS
        assert CliLoopState.__optional_keys__ == frozenset({"last_feishu_receive_chat_id"})

    def test_minimal_cli_state_has_all_required_keys(self):
        ctx = minimal_tick_ctx()
        state = minimal_cli_state(ctx)
        assert _EXPECTED_REQUIRED_KEYS.issubset(state.keys())

    def test_minimal_cli_state_omits_optional_keys(self):
        ctx = minimal_tick_ctx()
        state = minimal_cli_state(ctx)
        assert "last_feishu_receive_chat_id" not in state

    def test_type_annotations(self):
        ann = CliLoopState.__annotations__
        assert _forward_name(ann["skill_prompts"]) == "list[str]"
        assert _forward_name(ann["runtime_ctx"]) == "RuntimeContext"
        assert _forward_name(ann["session_manager"]) == "SessionManagerProtocol | None"
        assert _forward_name(ann["last_feishu_receive_chat_id"]) == "str"

    def test_optional_last_feishu_chat_id_roundtrip(self):
        ctx = minimal_tick_ctx()
        state = minimal_cli_state(ctx)
        state["last_feishu_receive_chat_id"] = "oc_test_chat"
        assert state["last_feishu_receive_chat_id"] == "oc_test_chat"


class TestMockCliStateFixture:
    """conftest mock_cli_state matches CliLoopState."""

    def test_mock_cli_state_fixture(self, mock_cli_state: CliLoopState):
        assert _EXPECTED_REQUIRED_KEYS.issubset(mock_cli_state.keys())
        assert mock_cli_state["active_session_id"] == "test_session"
        assert mock_cli_state["skill_prompts"] == []
        assert isinstance(mock_cli_state["feishu_p2p_synced_senders"], set)
