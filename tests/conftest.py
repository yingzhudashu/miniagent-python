"""Shared isolation fixtures for the MiniAgent test suite."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from miniagent.assistant.engine.cli_state import CliLoopState  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_json_config_from_user_file(request: pytest.FixtureRequest, tmp_path):
    """Keep unit tests independent from the developer's config.user.json."""
    if request.node.get_closest_marker("evaluation") is not None:
        yield
        return

    from miniagent.agent.settings import _CURRENT_SETTINGS, AgentSettings
    from miniagent.assistant.infrastructure import json_config

    previous = json_config.get_configuration_service()
    user_path = tmp_path / "default-user-config.json"
    user_path.write_text(json.dumps({}), encoding="utf-8")
    json_config.install_config_loader(
        json_config.JsonConfigLoader(defaults_path=None, user_path=str(user_path))
    )
    token = _CURRENT_SETTINGS.set(AgentSettings(json_config.get_config_snapshot()))
    try:
        yield
    finally:
        _CURRENT_SETTINGS.reset(token)
        json_config.install_configuration_service(previous)


@pytest.fixture(autouse=True)
def _reset_process_singletons_after_test():
    """Reset process-owned clients and caches after every test."""
    from miniagent.agent.executor import _reset_env_caches_for_tests
    from miniagent.agent.loop_detector import clear_args_cache

    yield

    _reset_env_caches_for_tests()
    clear_args_cache()
    try:
        from miniagent.assistant.infrastructure.instance import (
            reset_instance_registry_for_tests,
        )

        reset_instance_registry_for_tests()
    except ImportError:
        pass


@pytest.fixture
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Return an isolated state directory selected through the runtime override."""
    directory = str(tmp_path / "state")
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", directory)
    return directory


@pytest.fixture
def isolated_config_loader(tmp_path):
    """Build and install a JsonConfigLoader with optional user overrides."""
    from miniagent.assistant.infrastructure.json_config import (
        JsonConfigLoader,
        install_config_loader,
    )

    def factory(user_overrides: dict | None = None) -> JsonConfigLoader:
        user_path = tmp_path / "config.user.json"
        user_path.write_text(json.dumps(user_overrides or {}), encoding="utf-8")
        loader = JsonConfigLoader(defaults_path=None, user_path=str(user_path))
        install_config_loader(loader)
        return loader

    return factory


@pytest.fixture
def memory_runtime(state_dir: str):
    """Return a real, isolated memory object graph owned by the requesting test."""
    from miniagent.assistant.memory.runtime import create_memory_runtime

    runtime = create_memory_runtime(state_dir)
    yield runtime
    runtime.close()


@pytest.fixture
def knowledge_registry():
    """Return an explicitly injected empty knowledge registry."""
    from tests.support.memory import make_knowledge_registry

    return make_knowledge_registry()


@pytest.fixture
def mock_cli_state() -> CliLoopState:
    """Return the minimal CLI loop state used by command tests."""
    runtime = MagicMock()
    runtime.message_queue = MagicMock()
    runtime.channel_router = MagicMock()
    return {
        "active_session_id": "test_session",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": runtime,
        "feishu_p2p_synced_senders": set(),
    }
