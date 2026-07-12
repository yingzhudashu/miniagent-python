"""``scripts/docstring_inventory.py`` 扫描逻辑的轻量回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "docstring_inventory.py"


def _load_inventory_module():
    spec = importlib.util.spec_from_file_location("docstring_inventory", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def inv():
    return _load_inventory_module()


def test_scan_module_missing_docstring(inv, tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text("x = 1\n", encoding="utf-8")
    mod_ok, miss = inv.scan_file(p)
    assert mod_ok is False
    assert miss == []


def test_scan_function_missing_docstring(inv, tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        '"""mod."""\ndef foo():\n    return 1\n',
        encoding="utf-8",
    )
    mod_ok, miss = inv.scan_file(p)
    assert mod_ok is True
    assert [item.qualified_name for item in miss] == ["foo"]


def test_scan_skips_dunder_and_protocol_methods(inv, tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        '"""mod."""\n'
        "from typing import Protocol\n"
        "class C(Protocol):\n"
        "    def __repr__(self):\n"
        "        return 'C'\n"
        "    def __init__(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    mod_ok, miss = inv.scan_file(p)
    assert mod_ok is True
    names = [item.qualified_name for item in miss]
    assert "class C" in names
    assert not any("__repr__" in name for name in names)
    assert not any("__init__" in name for name in names)


def test_scan_ignores_local_closures_and_small_private_helpers(inv, tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        '\"\"\"mod.\"\"\"\n'
        "def _helper():\n"
        "    def local():\n"
        "        return 1\n"
        "    return local()\n",
        encoding="utf-8",
    )
    assert inv.scan_file(p) == (True, [])


def test_scan_ignores_small_protocol_overrides_on_private_classes(inv, tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        '"""mod."""\n'
        "class _Control:\n"
        "    def preferred_width(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    assert inv.scan_file(p) == (True, [])


def test_scan_requires_complex_private_state_machine_method(inv, tmp_path: Path) -> None:
    body = "\n".join("        value += 1" for _ in range(40))
    p = tmp_path / "m.py"
    p.write_text(
        '"""mod."""\n'
        "class _StateMachine:\n"
        "    def advance(self):\n"
        "        value = 0\n"
        f"{body}\n"
        "        return value\n",
        encoding="utf-8",
    )
    _, missing = inv.scan_file(p)
    assert [item.qualified_name for item in missing] == ["_StateMachine.advance"]
