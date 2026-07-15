"""setup_wizard 首次配置引导测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from miniagent.assistant.infrastructure.json_config import JsonConfigLoader, install_config_loader
from tests.config_helpers import DEFAULTS_PATH, install_test_config


def _install_loader_without_user_file(tmp_path: Path) -> Path:
    user_path = tmp_path / "config.user.json"
    install_config_loader(
        JsonConfigLoader(
            defaults_path=str(DEFAULTS_PATH),
            user_path=str(user_path),
        )
    )
    return user_path


def test_detect_first_time_setup_when_user_file_missing(tmp_path: Path) -> None:
    _install_loader_without_user_file(tmp_path)

    from miniagent.assistant.engine.setup_wizard import detect_first_time_setup

    assert detect_first_time_setup() is True


def test_detect_first_time_setup_when_user_file_exists(tmp_path: Path) -> None:
    install_test_config(tmp_path, {"model": {"model": "gpt-4o-mini"}})

    from miniagent.assistant.engine.setup_wizard import detect_first_time_setup

    assert detect_first_time_setup() is False


def test_save_setup_config_merges_sections(tmp_path: Path) -> None:
    user_path = tmp_path / "config.user.json"
    user_path.write_text(
        json.dumps(
            {
                "secrets": {"openai_api_key": "sk-old"},
                "model": {"model": "old-model", "base_url": "http://keep"},
            }
        ),
        encoding="utf-8",
    )
    install_test_config(tmp_path, user_path=user_path)

    from miniagent.assistant.engine.setup_wizard import save_setup_config

    save_setup_config(
        {
            "secrets": {"tavily_api_key": "tv-key"},
            "model": {"model": "gpt-4o"},
        }
    )

    data = json.loads(user_path.read_text(encoding="utf-8"))
    assert data["secrets"]["openai_api_key"] == "sk-old"
    assert data["secrets"]["tavily_api_key"] == "tv-key"
    assert data["model"]["model"] == "gpt-4o"
    assert data["model"]["base_url"] == "http://keep"


def test_save_setup_config_applies_reload_and_secrets(tmp_path: Path) -> None:
    user_path = _install_loader_without_user_file(tmp_path)

    from miniagent.assistant.engine import setup_wizard

    with (
        patch("miniagent.assistant.engine.setup_wizard.reload_config") as reload_mock,
        patch(
            "miniagent.assistant.infrastructure.env_loader.load_secrets_from_project_root"
        ) as secrets_mock,
        patch.object(setup_wizard, "_apply_saved_config", wraps=setup_wizard._apply_saved_config) as apply_mock,
    ):
        setup_wizard.save_setup_config({"secrets": {"openai_api_key": "sk-new"}})

    assert user_path.exists()
    reload_mock.assert_called_once()
    secrets_mock.assert_called_once()
    apply_mock.assert_called_once()


def test_run_setup_wizard_collects_user_input() -> None:
    from miniagent.assistant.engine.setup_wizard import run_setup_wizard

    inputs = [
        "1",
        "sk-test-key",
        "gpt-4o",
        "https://api.example.com/v1",
        "my-workspaces",
    ]
    with patch("builtins.input", side_effect=inputs):
        config = run_setup_wizard()

    assert config["secrets"]["llm"]["openai"]["api_key"] == "sk-test-key"
    assert config["llm"]["providers"]["openai"]["base_url"] == (
        "https://api.example.com/v1"
    )
    assert config["llm"]["models"]["primary"]["model"] == "gpt-4o"
    assert config["llm"]["roles"]["default"] == "primary"
    assert config["paths"] == {"state_dir": "my-workspaces"}


def test_run_setup_wizard_empty_api_key_does_not_write_secret() -> None:
    from miniagent.assistant.engine.setup_wizard import run_setup_wizard

    with patch("builtins.input", side_effect=["1", "", "", "", ""]):
        config = run_setup_wizard()

    assert "secrets" not in config


def test_run_interactive_setup_skipped_at_gate(tmp_path: Path) -> None:
    _install_loader_without_user_file(tmp_path)

    from miniagent.assistant.engine.setup_wizard import run_interactive_setup

    with patch("builtins.input", return_value="n"):
        ran = run_interactive_setup()

    assert ran is False
    assert not (tmp_path / "config.user.json").exists()


def test_run_interactive_setup_saves_config(tmp_path: Path) -> None:
    user_path = _install_loader_without_user_file(tmp_path)

    from miniagent.assistant.engine.setup_wizard import run_interactive_setup

    wizard_inputs = ["1", "sk-wizard", "", "", ""]
    gate_inputs = ["", *wizard_inputs]

    with patch("builtins.input", side_effect=gate_inputs):
        ran = run_interactive_setup()

    assert ran is True
    data = json.loads(user_path.read_text(encoding="utf-8"))
    assert data["secrets"]["llm"]["openai"]["api_key"] == "sk-wizard"


def test_apply_saved_config_reloads_config_and_secrets(tmp_path: Path) -> None:
    install_test_config(tmp_path, {"secrets": {"openai_api_key": "sk-x"}})

    with (
        patch("miniagent.assistant.engine.setup_wizard.reload_config") as reload_mock,
        patch(
            "miniagent.assistant.infrastructure.env_loader.load_secrets_from_project_root"
        ) as secrets_mock,
    ):
        from miniagent.assistant.engine.setup_wizard import _apply_saved_config

        _apply_saved_config()

    reload_mock.assert_called_once()
    secrets_mock.assert_called_once()
