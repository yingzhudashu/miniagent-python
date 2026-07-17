"""Enhanced TUI state, footer and configurable keybinding tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from miniagent.assistant.engine.model_cmd import get_current_model, switch_model_profile
from miniagent.llm.types import ModelCapabilities, ModelDescriptor
from miniagent.ui import TuiApp, TuiSnapshot
from miniagent.ui.cli.components import footer_text
from miniagent.ui.cli.keybindings import resolve_tui_keybindings
from tests.support.config import install_test_config


def test_keybinding_overrides_are_validated() -> None:
    resolved = resolve_tui_keybindings({"model_selector": "c-g"})
    assert resolved["model_selector"] == "c-g"
    with pytest.raises(ValueError, match="conflicting"):
        resolve_tui_keybindings({"model_selector": "c-o"})
    with pytest.raises(ValueError, match="unknown"):
        resolve_tui_keybindings({"coding_mode": "c-x"})


def test_footer_sheds_fields_for_narrow_terminals() -> None:
    model = ModelDescriptor(
        profile="primary",
        provider="openai",
        model="answer-model",
        api="openai_responses",
        capabilities=ModelCapabilities(reasoning=True),
    )
    ctx = SimpleNamespace(
        llm_gateway=SimpleNamespace(model_for_role=lambda _role: model)
    )
    state = {"active_session_id": "personal", "session_manager": None}
    view = TuiSnapshot(status="就绪")
    wide = footer_text(ctx, state, view, 120)
    narrow = footer_text(ctx, state, view, 28)
    assert "openai/answer-model" in wide
    assert "personal" in narrow
    assert len(narrow) <= 28


def test_reasoning_visibility_is_explicit_state() -> None:
    view = TuiApp(SimpleNamespace(), TuiSnapshot())
    assert view.reasoning_expanded is True
    assert view.toggle_reasoning() is False


def test_switch_model_profile_persists_v3_role(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {
            "llm": {
                "providers": {"openai": {"driver": "openai"}},
                "models": {
                    "first": {
                        "provider": "openai",
                        "model": "first-model",
                        "api": "openai_chat",
                    },
                    "second": {
                        "provider": "openai",
                        "model": "second-model",
                        "api": "openai_chat",
                    },
                },
                "roles": {"default": "first"},
            }
        },
    )
    result = switch_model_profile("second")
    assert "second" in result
    assert get_current_model() == "second-model"
    document = json.loads((tmp_path / "config.user.json").read_text(encoding="utf-8"))
    assert document["llm"]["roles"]["default"] == "second"
