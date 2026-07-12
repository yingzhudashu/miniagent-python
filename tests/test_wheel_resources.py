"""Wheel 内容校验器回归测试。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.check_wheel_resources import REQUIRED_RESOURCES, check_wheel


def _write_wheel(path: Path, names: set[str]) -> None:
    """写入仅包含指定路径的最小测试 Wheel 压缩包。"""
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "test")


def test_check_wheel_rejects_stale_and_missing_python_modules(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    package_root = source_root / "miniagent"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "current.py").write_text("", encoding="utf-8")
    wheel = tmp_path / "package.whl"
    _write_wheel(
        wheel,
        set(REQUIRED_RESOURCES)
        | {
            "miniagent/__init__.py",
            "miniagent/stale.py",
        },
    )

    issues = check_wheel(wheel, source_root=source_root)

    assert "missing Python module: miniagent/current.py" in issues
    assert "stale Python module: miniagent/stale.py" in issues


def test_check_wheel_accepts_matching_source_tree(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    package_root = source_root / "miniagent"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    wheel = tmp_path / "package.whl"
    _write_wheel(wheel, set(REQUIRED_RESOURCES) | {"miniagent/__init__.py"})

    assert check_wheel(wheel, source_root=source_root) == []
