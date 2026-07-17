"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_parse_activity_log_with_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.assistant.self_opt.runtime_analyzer as analyzer

    monkeypatch.setattr(analyzer, "get_activity_log_dir", lambda: tmp_path)
    (tmp_path / "2026-07-12.md").write_text("## session-1\n", encoding="utf-8")
    result = analyzer.parse_activity_log("2026-07-12")
    assert result["sessions"] == ["session-1"]
