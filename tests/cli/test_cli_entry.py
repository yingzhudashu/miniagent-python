"""Tests for CLI packaging entry (``miniagent.assistant.cli.cli``) and ``--help``."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.runner import _print_cli_help, _wants_help


def test_wants_help_detects_flags() -> None:
    assert _wants_help(["miniagent", "--help"])
    assert _wants_help(["-m", "miniagent", "-h"])
    assert not _wants_help(["miniagent", "--doctor"])
    assert not _wants_help(["miniagent", "--session", "foo"])


def test_print_cli_help_includes_usage(capsys) -> None:
    _print_cli_help()
    out = capsys.readouterr().out
    assert "用法:" in out
    assert "--stop" in out
    assert "--doctor" in out
    assert "miniagent.assistant.cli.cli" in out


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, "-m", "miniagent", "--help"],
        [sys.executable, "-m", "miniagent", "-h"],
        [sys.executable, "-m", "miniagent.assistant.cli.cli", "--help"],
    ],
)
def test_help_flag_exits_zero(argv: list[str]) -> None:
    result = subprocess.run(
        argv,
        capture_output=True,
        timeout=15,
    )
    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8")
    assert result.returncode == 0, stderr
    assert "用法:" in stdout
    assert "Traceback" not in stderr
    assert "RuntimeWarning" not in stderr


def test_cli_main_delegates_to_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_entry(_argv=None) -> None:
        called.append(True)

    monkeypatch.setattr("miniagent.assistant.run_assistant", fake_entry)

    from miniagent.assistant.cli.cli import main

    main()
    assert called == [True]


def test_secret_loader_delegates_to_bootstrap_use_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.bootstrap.configuration as configuration
    import miniagent.assistant.runner as runner

    load = MagicMock()
    monkeypatch.setattr(configuration, "load_secrets_from_project_root", load)
    runner._load_env()
    load.assert_called_once_with()


def test_normal_cli_path_builds_and_runs_personal_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.app as app_module
    import miniagent.assistant.bootstrap.configuration as configuration
    import miniagent.assistant.engine.setup_wizard as setup_wizard
    import miniagent.assistant.runner as runner

    application = MagicMock()
    monkeypatch.setattr(runner, "_load_env", MagicMock())
    monkeypatch.setattr(runner, "_bootstrap_project_paths", MagicMock())
    monkeypatch.setattr(setup_wizard, "run_interactive_setup", MagicMock())
    monkeypatch.setattr(configuration, "load_secrets_from_project_root", MagicMock())
    monkeypatch.setattr(app_module, "create_personal_assistant", lambda: application)
    monkeypatch.setattr(sys, "argv", ["miniagent", "--no-continue"])

    runner._run_current_argv()

    setup_wizard.run_interactive_setup.assert_called_once_with()
    configuration.load_secrets_from_project_root.assert_called_once_with()
    application.run.assert_called_once_with()
