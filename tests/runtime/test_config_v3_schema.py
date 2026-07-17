"""Current 3.0 configuration rejects removed configuration shapes."""

from __future__ import annotations

import json

import pytest

from miniagent.assistant.infrastructure.json_config import JsonConfigLoader


def _loader(tmp_path, user: dict) -> JsonConfigLoader:
    defaults = tmp_path / "defaults.json"
    defaults.write_text(
        json.dumps(
            {
                "version": "3.0.0",
                "llm": {"providers": {}, "models": {}, "roles": {}},
                "secrets": {"llm": {}},
            }
        ),
        encoding="utf-8",
    )
    user_path = tmp_path / "config.user.json"
    user_path.write_text(json.dumps(user), encoding="utf-8")
    return JsonConfigLoader(str(defaults), str(user_path))


@pytest.mark.parametrize(
    "removed",
    [
        {"model": {"model": "gpt-old"}},
        {"secrets": {"openai_api_key": "removed"}},
    ],
)
def test_removed_configuration_paths_are_rejected_without_rewrite(tmp_path, removed) -> None:
    loader = _loader(tmp_path, removed)
    before = loader.paths[1].read_bytes()
    with pytest.raises(ValueError, match="未知配置项"):
        loader.snapshot()
    assert loader.paths[1].read_bytes() == before


def test_current_provider_profile_role_configuration_loads(tmp_path) -> None:
    loader = _loader(
        tmp_path,
        {"llm": {"providers": {}, "models": {}, "roles": {}}, "secrets": {"llm": {}}},
    )
    assert loader.snapshot().get_path("llm.roles") == {}
