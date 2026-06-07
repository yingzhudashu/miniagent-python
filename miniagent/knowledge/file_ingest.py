"""Project-level RAG ingestion for files read during analysis.

This module stores text files in a normal ``KnowledgeBase`` directory so the
existing registry/search stack can reuse them without a new storage format.
Only metadata and content hashes are traced; file contents stay on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from miniagent.core.constants import KNOWLEDGE_MAX_FILE_CHARS
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.trace_events import EVENT_KNOWLEDGE_FILE_INGEST
from miniagent.infrastructure.tracing import emit_trace

_DEFAULT_KB_NAME = "_auto_file_analysis"
_METADATA_FILE = "source-metadata.json"


@dataclass
class IngestResult:
    """Result of adding a source file to the automatic analysis knowledge base."""

    success: bool
    skipped: bool = False
    reason: str = ""
    kb_name: str = ""
    kb_path: str = ""
    file_path: str = ""
    source_path: str = ""
    source_hash: str = ""
    changed: bool = False
    size: int = 0


def auto_file_ingest_enabled() -> bool:
    """Return whether read/analyze file calls should update the auto RAG KB."""
    return bool(get_config("knowledge.auto_ingest_analyzed_files", True))


def auto_file_kb_name() -> str:
    """Configured name for the project-level automatic file-analysis KB."""
    return str(get_config("knowledge.auto_ingest_kb_name", _DEFAULT_KB_NAME) or _DEFAULT_KB_NAME)


def ensure_auto_file_kb(*, kb_name: str | None = None) -> str:
    """Create the automatic knowledge base directory and KB.yaml if needed."""
    name = kb_name or auto_file_kb_name()
    kb_root = get_config(
        "knowledge.root",
        get_config("knowledge.default_root", "workspaces/knowledge"),
    )
    kb_path = Path(str(kb_root)) / name
    files_dir = kb_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    config_path = kb_path / "KB.yaml"
    if not config_path.exists():
        config_path.write_text(
            "\n".join(
                [
                    f"name: {name}",
                    "description: Automatically ingested source files for repeated file analysis.",
                    "retriever: keyword",
                    "max_chars: 8000",
                    "top_k: 5",
                    "file_patterns:",
                    "  - '*.md'",
                    "  - '*.txt'",
                    "  - '*.json'",
                    "  - '*.py'",
                    "  - '*.yaml'",
                    "  - '*.yml'",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return str(kb_path)


def ingest_file_for_analysis(
    path: str,
    *,
    state_dir: str | None = None,
    kb_name: str | None = None,
    content: str | None = None,
) -> IngestResult:
    """Persist a text source file into the project-level analysis knowledge base."""
    start_ns = time.monotonic_ns()
    name = kb_name or auto_file_kb_name()
    source = Path(path).resolve()
    result = IngestResult(success=False, kb_name=name, source_path=str(source))
    try:
        if not auto_file_ingest_enabled():
            result.skipped = True
            result.reason = "disabled"
            return result
        if not source.is_file():
            result.skipped = True
            result.reason = "not_file"
            return result

        stat = source.stat()
        result.size = int(stat.st_size)
        max_chars = get_config("knowledge.auto_ingest_max_file_chars", None)
        if max_chars is None:
            max_chars = get_config("knowledge.max_file_chars", KNOWLEDGE_MAX_FILE_CHARS)
        max_chars = int(max_chars)

        if content is None:
            try:
                content = source.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                result.skipped = True
                result.reason = "non_utf8"
                return result
        if "\x00" in content:
            result.skipped = True
            result.reason = "binary"
            return result

        source_hash = _sha256_text(content)
        result.source_hash = source_hash
        kb_path = Path(ensure_auto_file_kb(kb_name=name))
        result.kb_path = str(kb_path)
        files_dir = kb_path / "files"
        metadata_path = kb_path / _METADATA_FILE
        metadata = _load_metadata(metadata_path)
        source_key = str(source)
        existing = metadata.get(source_key)

        extension = source.suffix or ".txt"
        mirror_name = f"{_sha256_text(source_key)[:16]}{extension}"
        mirror_path = files_dir / mirror_name
        result.file_path = mirror_name
        result.changed = not existing or existing.get("source_hash") != source_hash

        if not result.changed and mirror_path.exists():
            result.success = True
            result.skipped = True
            result.reason = "unchanged"
            return result

        ingest_content = content[:max_chars]
        if len(content) > max_chars:
            ingest_content += "\n...[已截断]"
        mirror_path.write_text(ingest_content, encoding="utf-8")
        metadata[source_key] = {
            "source_path": source_key,
            "file_path": mirror_name,
            "source_hash": source_hash,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "ingested_at": time.time(),
            "display_path": _display_path(source_key, state_dir),
        }
        _save_metadata(metadata_path, metadata)
        _refresh_registry(name, str(kb_path))

        result.success = True
        return result
    except Exception as exc:
        result.reason = type(exc).__name__
        return result
    finally:
        emit_trace(
            {
                "type": EVENT_KNOWLEDGE_FILE_INGEST,
                "success": result.success,
                "skipped": result.skipped,
                "reason": result.reason,
                "kb_name": result.kb_name,
                "changed": result.changed,
                "size": result.size,
                "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
            }
        )


def load_auto_file_metadata(kb_path: str) -> dict[str, dict[str, Any]]:
    """Load metadata for an automatic file-analysis KB."""
    return _load_metadata(Path(kb_path) / _METADATA_FILE)


def _refresh_registry(kb_name: str, kb_path: str) -> None:
    try:
        from miniagent.knowledge.registry import get_kb_registry

        registry = get_kb_registry()
        refresh = getattr(registry, "refresh_auto_file_kb", None)
        if callable(refresh):
            refresh(kb_path, kb_name)
        else:
            registry.mount(kb_path, kb_name)
    except Exception:
        pass


def _load_metadata(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        backup = path.with_suffix(".corrupt.json")
        try:
            shutil.copy2(path, backup)
        except Exception:
            pass
        return {}


def _save_metadata(path: Path, metadata: dict[str, dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2, sort_keys=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _display_path(source_path: str, state_dir: str | None) -> str:
    if state_dir:
        try:
            return os.path.relpath(source_path, state_dir)
        except ValueError:
            pass
    return source_path


__all__ = [
    "IngestResult",
    "auto_file_ingest_enabled",
    "auto_file_kb_name",
    "ensure_auto_file_kb",
    "ingest_file_for_analysis",
    "load_auto_file_metadata",
]
