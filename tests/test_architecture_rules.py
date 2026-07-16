"""Regression tests for the complete four-module architecture checker."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_architecture.py"


@pytest.fixture(scope="module")
def architecture_module():
    spec = importlib.util.spec_from_file_location("check_architecture", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_module(root: Path, package: str, source: str, name: str = "sample.py") -> None:
    directory = root / package
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("llm", "agent"),
        ("llm", "assistant"),
        ("agent", "assistant"),
        ("agent", "ui"),
        ("ui", "agent"),
        ("ui", "assistant"),
    ],
)
def test_forbidden_dependency_directions(
    architecture_module, tmp_path: Path, source: str, target: str
) -> None:
    _write_module(tmp_path, source, f"from miniagent.{target}.sample import value\n")
    violations = architecture_module.check_architecture(tmp_path)
    dependencies = [item for item in violations if isinstance(item, architecture_module.Violation)]
    assert [(item.source_package, item.imported_module) for item in dependencies] == [
        (source, f"miniagent.{target}.sample")
    ]


def test_function_local_import_cannot_bypass_rule(architecture_module, tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "agent",
        "def delayed():\n    from miniagent.assistant.app import run_assistant\n",
    )
    violations = architecture_module.check_architecture(tmp_path)
    assert any(
        isinstance(item, architecture_module.Violation) and item.line == 2
        for item in violations
    )


def test_relative_import_cannot_bypass_rule(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "agent", "from ..assistant.app import run_assistant\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert any(
        isinstance(item, architecture_module.Violation)
        and item.imported_module == "miniagent.assistant.app"
        for item in violations
    )


def test_type_checking_import_still_obeys_layering(architecture_module, tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "ui",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from miniagent.agent.runtime import Agent\n",
    )
    assert any(
        isinstance(item, architecture_module.Violation)
        for item in architecture_module.check_architecture(tmp_path)
    )


def test_unknown_top_level_package_is_rejected(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "core", "value = 1\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert any(isinstance(item, architecture_module.TopologyViolation) for item in violations)


def test_cross_layer_cycle_is_reported(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "agent", "from miniagent.assistant.app import value\n")
    _write_module(tmp_path, "assistant", "from miniagent.agent.runtime import value\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert any(isinstance(item, architecture_module.CycleViolation) for item in violations)


def test_command_module_cannot_import_dispatcher(architecture_module, tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "assistant/engine/commands",
        "from miniagent.assistant.engine.command_dispatch import dispatch_command\n",
    )
    violations = architecture_module.check_architecture(tmp_path)
    assert any(
        isinstance(item, architecture_module.ModuleDependencyViolation)
        and "command dispatcher" in item.message
        for item in violations
    )


def test_assistant_production_uses_agent_facade(architecture_module, tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "assistant/engine",
        "from miniagent.agent.agent import run_agent\n",
    )
    violations = architecture_module.check_architecture(tmp_path)
    assert any(
        isinstance(item, architecture_module.ModuleDependencyViolation)
        and "Agent facade" in item.message
        for item in violations
    )


def test_current_repository_obeys_four_module_rules(architecture_module) -> None:
    assert architecture_module.check_architecture(REPO_ROOT / "miniagent") == []


def _function_source(total_lines: int) -> str:
    return "def sample():\n" + "\n".join("    pass" for _ in range(total_lines - 1)) + "\n"


def test_function_length_limit_rejects_101_lines(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "agent", _function_source(101))
    violations = architecture_module.check_architecture(tmp_path, rules=())
    assert len(violations) == 1
    assert violations[0].function_name == "sample"


def test_function_length_limit_accepts_100_lines(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "agent", _function_source(100))
    assert architecture_module.check_architecture(tmp_path, rules=()) == []
