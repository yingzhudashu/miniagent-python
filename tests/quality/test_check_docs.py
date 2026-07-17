"""文档一致性检查脚本的回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker():
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py"
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


def test_check_docs_rejects_retired_audit_and_matrix(
    tmp_path: Path,
) -> None:
    checker = _load_checker()
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (docs / "PERFORMANCE_AUDIT.md").write_text("# Performance Audit\n", encoding="utf-8")
    (docs / "INDEX.md").write_text(
        "# Index\n\n[Audit](PERFORMANCE_AUDIT.md)\n\nTEST_COVERAGE_MATRIX.md\n",
        encoding="utf-8",
    )

    issues = checker.check_docs(tmp_path)

    assert any("PERFORMANCE_AUDIT.md" in issue for issue in issues)
    assert any("TEST_COVERAGE_MATRIX.md" in issue for issue in issues)


def test_check_docs_reports_version_command_and_process_artifact_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    checker = _load_checker()
    docs = tmp_path / "docs"
    package = tmp_path / "miniagent"
    registry = package / "assistant" / "engine"
    docs.mkdir()
    registry.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "3.0.0"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Project\n\n![Version](https://img.shields.io/badge/version-2.0.0-blue)\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (docs / "CLI.md").write_text(
        "# CLI\n\n> Project | 版本: 2.0.0\n\n`/help`\n", encoding="utf-8"
    )
    (docs / "INDEX.md").write_text("# Index\n\n[CLI](CLI.md)\n", encoding="utf-8")
    (registry / "command_registry.py").write_text(
        'CommandSpec("/help", "help", "help", "/help")\n'
        'CommandSpec("/copy", None, "copy", "/copy")\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "_git_review_surface", lambda _root: ("coverage.xml",))

    issues = checker.check_docs(tmp_path)

    assert any("版本徽章" in issue for issue in issues)
    assert any("文档版本 2.0.0" in issue for issue in issues)
    assert any("未说明注册命令 /copy" in issue for issue in issues)
    assert any("过程性产物" in issue for issue in issues)
