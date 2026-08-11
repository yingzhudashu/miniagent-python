"""Tests for automatic file-analysis knowledge ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from miniagent.assistant.knowledge.base import KnowledgeBase
from miniagent.assistant.knowledge.file_ingest import _load_metadata, ingest_file_for_analysis
from miniagent.assistant.knowledge.registry import KnowledgeRegistry


def _cfg(root: Path):
    def _get_config(key: str, default=None):
        if key in {"knowledge.root", "knowledge.default_root"}:
            return str(root)
        return default

    return _get_config


def test_ingest_file_for_analysis_creates_auto_kb(tmp_path: Path) -> None:
    kb_root = tmp_path / "knowledge"
    source = tmp_path / "src" / "notes.md"
    source.parent.mkdir()
    source.write_text("# Notes\n\nAlpha Beta API", encoding="utf-8")

    registry = KnowledgeRegistry(state_dir=str(tmp_path))
    registry._mounted.clear()
    with patch("miniagent.assistant.knowledge.file_ingest.get_config", side_effect=_cfg(kb_root)):
        result = ingest_file_for_analysis(
            str(source), registry=registry, state_dir=str(tmp_path)
        )

    assert result.success
    assert result.changed
    assert result.kb_name == "_auto_file_analysis"
    assert Path(result.kb_path, "KB.yaml").is_file()
    assert Path(result.kb_path, "files", result.file_path).is_file()
    metadata = json.loads(Path(result.kb_path, "source-metadata.json").read_text(encoding="utf-8"))
    assert str(source.resolve()) in metadata
    assert metadata[str(source.resolve())]["source_hash"] == result.source_hash


def test_ingest_unchanged_file_skips_duplicate_write(tmp_path: Path) -> None:
    kb_root = tmp_path / "knowledge"
    source = tmp_path / "a.txt"
    source.write_text("same content", encoding="utf-8")

    registry = KnowledgeRegistry(state_dir=str(tmp_path))
    registry._mounted.clear()
    with patch("miniagent.assistant.knowledge.file_ingest.get_config", side_effect=_cfg(kb_root)):
        first = ingest_file_for_analysis(str(source), registry=registry)
        second = ingest_file_for_analysis(str(source), registry=registry)

    assert first.success
    assert second.success
    assert second.skipped
    assert second.reason == "unchanged"


def test_ingest_changed_file_refreshes_searchable_content(tmp_path: Path) -> None:
    kb_root = tmp_path / "knowledge"
    source = tmp_path / "a.md"
    source.write_text("old keyword", encoding="utf-8")
    registry = KnowledgeRegistry(state_dir=str(tmp_path))
    registry._mounted.clear()
    with patch("miniagent.assistant.knowledge.file_ingest.get_config", side_effect=_cfg(kb_root)):
        first = ingest_file_for_analysis(str(source), registry=registry)

        source.write_text("new keyword zeta", encoding="utf-8")
        second = ingest_file_for_analysis(str(source), registry=registry)
    kb = KnowledgeBase(second.kb_path)
    result = kb.search("zeta")

    assert first.source_hash != second.source_hash
    assert second.changed
    assert "zeta" in result
    assert str(source.resolve()) in result
    assert "zeta" in registry.search("zeta", kb_name=second.kb_name)


def test_corrupt_metadata_is_reported_even_when_backup_fails(tmp_path: Path) -> None:
    metadata = tmp_path / "source-metadata.json"
    metadata.write_text("not-json", encoding="utf-8")

    with patch(
        "miniagent.assistant.knowledge.file_ingest.shutil.copy2",
        side_effect=OSError("backup failed"),
    ):
        assert _load_metadata(metadata) == {}
