"""merge_agent_config 回归：session_registry 与 risk_level 不得丢失。"""

from __future__ import annotations

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.infrastructure.registry import DefaultToolRegistry


def test_merge_preserves_session_registry() -> None:
    reg = DefaultToolRegistry()
    base = get_default_agent_config()
    base = merge_agent_config(base, {"session_registry": reg})
    merged = merge_agent_config(base, {"max_turns": 3})
    assert merged.session_registry is reg
    assert merged.max_turns == 3


def test_merge_allows_session_registry_override() -> None:
    r1 = DefaultToolRegistry()
    r2 = DefaultToolRegistry()
    base = merge_agent_config(get_default_agent_config(), {"session_registry": r1})
    merged = merge_agent_config(base, {"session_registry": r2})
    assert merged.session_registry is r2


def test_merge_preserves_risk_level() -> None:
    base = merge_agent_config(get_default_agent_config(), {"risk_level": "high"})
    merged = merge_agent_config(base, {"debug": True})
    assert merged.risk_level == "high"
    assert merged.debug is True
