"""Backup-first v2 to v3 LLM configuration migration tests."""

from __future__ import annotations

import json
from pathlib import Path

from miniagent.infrastructure.config_migration import (
    migrate_config_file,
    migrate_v2_document,
)
from miniagent.infrastructure.json_config import JsonConfigLoader
from tests.config_helpers import DEFAULTS_PATH


def _legacy() -> dict:
    return {
        "secrets": {"openai_api_key": "secret", "tavily_api_key": "search"},
        "model": {
            "base_url": "https://example.test/v1",
            "model": "answer-model",
            "wire_api": "responses",
            "max_tokens": 8192,
            "context_window": 200000,
            "temperature": 0.2,
        },
        "features": {"reflection": True},
    }


def test_migrate_v2_document_preserves_unrelated_sections() -> None:
    source = _legacy()
    result = migrate_v2_document(source)
    assert result.changed is True
    assert "model" not in result.document
    assert result.document["features"] == source["features"]
    assert result.document["llm"]["models"]["primary"]["api"] == "openai_responses"
    assert result.document["secrets"]["llm"]["openai"]["api_key"] == "secret"
    assert result.document["secrets"]["tavily_api_key"] == "search"
    assert "openai_api_key" not in result.document["secrets"]


def test_migration_is_idempotent() -> None:
    first = migrate_v2_document(_legacy())
    second = migrate_v2_document(first.document)
    assert second.changed is False
    assert second.document == first.document


def test_dry_run_does_not_write_and_write_creates_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.user.json"
    original = json.dumps(_legacy(), ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    preview = migrate_config_file(path, write=False)
    assert preview.changed is True
    assert path.read_text(encoding="utf-8") == original
    written = migrate_config_file(path, write=True)
    assert written.backup_path is not None
    assert written.backup_path.read_text(encoding="utf-8") == original
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == "3.0.0"


def test_invalid_legacy_sections_are_rejected() -> None:
    try:
        migrate_v2_document({"model": []})
    except ValueError as error:
        assert "model section" in str(error)
    else:
        raise AssertionError("invalid model section should fail")


def test_strict_config_accepts_custom_provider_and_model_maps(tmp_path: Path) -> None:
    path = tmp_path / "config.user.json"
    path.write_text(
        json.dumps(
            {
                "secrets": {"llm": {"custom": {"api_key": "secret"}}},
                "llm": {
                    "providers": {
                        "custom": {
                            "driver": "openai",
                            "base_url": "https://example.test/v1",
                            "credential": "custom",
                            "headers": {"X-Client": "miniagent"},
                        }
                    },
                    "models": {
                        "custom-model": {
                            "provider": "custom",
                            "model": "answer",
                            "api": "openai_chat",
                            "compatibility": {"supports_store": False},
                        }
                    },
                    "roles": {"default": "custom-model"},
                },
            }
        ),
        encoding="utf-8",
    )
    loader = JsonConfigLoader(defaults_path=str(DEFAULTS_PATH), user_path=str(path))
    loader.reload(strict=True)
