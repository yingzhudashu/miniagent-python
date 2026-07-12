"""文档一致性检查脚本的回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker():
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_docs.py"
    spec = importlib.util.spec_from_file_location("check_docs", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_docs_accepts_linked_index(tmp_path: Path) -> None:
    checker = _load_checker()
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Project\n\n[Guide](docs/GUIDE.md#使用)\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (tmp_path / "docs" / "GUIDE.md").write_text("# Guide\n\n## 使用\n", encoding="utf-8")
    (tmp_path / "docs" / "INDEX.md").write_text(
        "# Index\n\n[Guide](GUIDE.md)\n", encoding="utf-8"
    )
    assert checker.check_docs(tmp_path) == []


def test_check_docs_reports_missing_target_and_hardcoded_count(tmp_path: Path) -> None:
    checker = _load_checker()
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        "# Project\n\n[Missing](docs/nope.md)\n\n2479 tests passed.\n", encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (tmp_path / "docs" / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    issues = checker.check_docs(tmp_path)
    assert any("本地链接不存在" in issue for issue in issues)
    assert any("硬编码测试数量" in issue for issue in issues)
