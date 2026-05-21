"""已移除的 MINIAGENT_CONFIG / MINIAGENT_OPENCLAW_CONFIG 环境变量警告。"""

from unittest.mock import patch

import pytest

from miniagent.core.config import get_default_model_config
from miniagent.infrastructure.env_loader import (
    _warn_removed_external_config_env,
    load_dotenv_from_project_root,
    reset_removed_external_config_warnings_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_warn_flag() -> None:
    reset_removed_external_config_warnings_for_tests()
    yield
    reset_removed_external_config_warnings_for_tests()


def test_removed_config_env_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CONFIG", "/tmp/legacy.json")
    with patch("miniagent.infrastructure.env_loader._logger.warning") as warn:
        load_dotenv_from_project_root()
        warn.assert_called_once()
        assert "MINIAGENT_CONFIG" in str(warn.call_args[0])
        assert "已不再支持" in str(warn.call_args[0])
    with patch("miniagent.infrastructure.env_loader._logger.warning") as warn2:
        load_dotenv_from_project_root()
        warn2.assert_not_called()


def test_warn_direct_openclaw_legacy_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIAGENT_CONFIG", raising=False)
    monkeypatch.setenv("MINIAGENT_OPENCLAW_CONFIG", "/tmp/old.json")
    with patch("miniagent.infrastructure.env_loader._logger.warning") as warn:
        _warn_removed_external_config_env()
        warn.assert_called_once()
        assert "MINIAGENT_OPENCLAW_CONFIG" in str(warn.call_args[0])


def test_removed_config_does_not_affect_model_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_CONFIG", "/tmp/legacy.json")
    monkeypatch.setenv("OPENAI_MODEL", "env-only-model")
    monkeypatch.delenv("AGENT_THINKING_DEFAULT", raising=False)
    monkeypatch.delenv("OPENAI_THINKING_BUDGET", raising=False)
    mc = get_default_model_config()
    assert mc.model == "env-only-model"
