"""Tests for CLI packaging entry (``miniagent.cli.cli``) and ``--help``."""

from __future__ import annotations

import subprocess
import sys

import pytest

from miniagent.__main__ import _print_cli_help, _wants_help


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
    assert "miniagent.cli.cli" in out


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, "-m", "miniagent", "--help"],
        [sys.executable, "-m", "miniagent", "-h"],
        [sys.executable, "-m", "miniagent.cli.cli", "--help"],
    ],
)
def test_help_flag_exits_zero(argv: list[str]) -> None:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "用法:" in result.stdout
    assert "Traceback" not in result.stderr
    assert "RuntimeWarning" not in result.stderr


def test_cli_main_delegates_to_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_entry() -> None:
        called.append(True)

    monkeypatch.setattr("miniagent.__main__.main", fake_entry)

    from miniagent.cli.cli import main

    main()
    assert called == [True]
