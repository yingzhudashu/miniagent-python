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
    assert "foo" in miss


def test_scan_skips_dunder_except_init(inv, tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        '"""mod."""\n'
        "class C:\n"
        "    def __repr__(self):\n"
        "        return 'C'\n"
        "    def __init__(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    mod_ok, miss = inv.scan_file(p)
    assert mod_ok is True
    assert "class C" in miss  # class without docstring
    assert not any("__repr__" in m for m in miss)
    assert any("__init__" in m for m in miss)
