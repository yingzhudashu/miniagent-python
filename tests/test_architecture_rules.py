"""Regression tests for the architecture dependency checker."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_architecture.py"


@pytest.fixture(scope="module")
def architecture_module():
    """Load the repository script without requiring scripts to be a package."""
    spec = importlib.util.spec_from_file_location("check_architecture", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_module(root: Path, package: str, source: str) -> None:
    """Create a small source package used by an architecture rule test."""
    directory = root / package
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sample.py").write_text(source, encoding="utf-8")


def test_contracts_cannot_import_runtime_layers(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "contracts", "from miniagent.engine.main import run_runtime\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert [(v.source_package, v.imported_module) for v in violations] == [
        ("contracts", "miniagent.engine.main")
    ]


def test_application_can_only_depend_on_contracts(architecture_module, tmp_path: Path) -> None:
    _write_module(
        tmp_path, "application", "from miniagent.infrastructure.logger import get_logger\n"
    )
    violations = architecture_module.check_architecture(tmp_path)
    assert [(v.source_package, v.imported_module) for v in violations] == [
        ("application", "miniagent.infrastructure.logger")
    ]


def test_types_cannot_eagerly_import_feishu(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "types", "from miniagent.feishu.types import FeishuConfig\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert len(violations) == 1
    assert violations[0].source_package == "types"


def test_core_cannot_import_presentation(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "core", "from miniagent.presentation.cli.state import TuiViewState\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert [(v.source_package, v.imported_module) for v in violations] == [
        ("core", "miniagent.presentation.cli.state")
    ]


def test_presentation_cannot_import_runtime_layers(
    architecture_module, tmp_path: Path
) -> None:
    _write_module(tmp_path, "presentation", "from miniagent.engine.main import run_runtime\n")
    violations = architecture_module.check_architecture(tmp_path)
    assert [(v.source_package, v.imported_module) for v in violations] == [
        ("presentation", "miniagent.engine.main")
    ]


def test_type_checking_and_function_local_imports_are_allowed(
    architecture_module, tmp_path: Path
) -> None:
    _write_module(
        tmp_path,
        "types",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from miniagent.feishu.types import FeishuConfig\n"
        "def load_type():\n"
        "    from miniagent.feishu.types import FeishuConfig\n"
        "    return FeishuConfig\n",
    )
    assert architecture_module.check_architecture(tmp_path) == []


def test_current_repository_obeys_enabled_rules(architecture_module) -> None:
    assert architecture_module.check_architecture(REPO_ROOT / "miniagent") == []


def _function_source(total_lines: int) -> str:
    """构造 AST 行数精确可控的函数源码。"""
    return "def sample():\n" + "\n".join("    pass" for _ in range(total_lines - 1)) + "\n"


def test_function_length_limit_rejects_101_lines(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "core", _function_source(101))
    violations = architecture_module.check_architecture(tmp_path, rules=())
    assert len(violations) == 1
    assert violations[0].function_name == "sample"
    assert violations[0].length == 101


def test_function_length_limit_accepts_100_lines(architecture_module, tmp_path: Path) -> None:
    _write_module(tmp_path, "core", _function_source(100))
    assert architecture_module.check_architecture(tmp_path, rules=()) == []
