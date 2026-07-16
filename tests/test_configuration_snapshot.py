"""不可变配置快照的递归冻结与点路径读取测试。"""

from __future__ import annotations

import pytest

from miniagent.agent.settings import AgentSettings
from miniagent.assistant.contracts.configuration import ConfigSnapshot
from miniagent.assistant.infrastructure.json_config import JsonConfigLoader


def test_config_snapshot_recursively_freezes_input() -> None:
    source = {"model": {"names": ["a", {"nested": True}]}}
    snapshot = ConfigSnapshot(source)
    source["model"]["names"].append("later")

    assert list(snapshot) == ["model"]
    assert len(snapshot) == 1
    assert snapshot.get_path("model.names")[0] == "a"
    assert snapshot.get_path("missing", "fallback") == "fallback"
    with pytest.raises(TypeError):
        snapshot["model"]["new"] = 1


def test_agent_settings_reuses_an_existing_frozen_snapshot() -> None:
    snapshot = ConfigSnapshot({"agent": {"max_turns": 3}})
    settings = AgentSettings(snapshot)
    assert settings._values is snapshot._values


def test_runtime_overrides_survive_snapshot_without_disk_rewrite(tmp_path) -> None:
    user_path = tmp_path / "config.user.json"
    user_path.write_text("{}", encoding="utf-8")
    loader = JsonConfigLoader(user_path=str(user_path))
    loader.reload(strict=True)

    isolated = loader.with_runtime_overrides(
        {
            "trace": {"enabled": True, "output_dir": str(tmp_path / "trace")},
            "paths": {"state_dir": str(tmp_path / "state")},
        }
    )
    snapshot = isolated.snapshot()

    assert snapshot.get_path("trace.enabled") is True
    assert snapshot.get_path("trace.output_dir") == str(tmp_path / "trace")
    assert snapshot.get_path("paths.state_dir") == str(tmp_path / "state")
    assert user_path.read_text(encoding="utf-8") == "{}"


def test_snapshot_only_observes_disk_changes_after_explicit_reload(tmp_path) -> None:
    user_path = tmp_path / "config.user.json"
    user_path.write_text("{}", encoding="utf-8")
    loader = JsonConfigLoader(user_path=str(user_path))

    assert loader.snapshot().get_path("trace.enabled") is False
    user_path.write_text('{"trace":{"enabled":true}}', encoding="utf-8")
    assert loader.snapshot().get_path("trace.enabled") is False

    loader.reload(strict=True)
    assert loader.snapshot().get_path("trace.enabled") is True
