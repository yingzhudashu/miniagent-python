"""Explicit, backup-first migration from the v2 model config to the v3 LLM schema."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json


@dataclass(frozen=True, slots=True)
class ConfigMigrationResult:
    changed: bool
    document: dict[str, Any]
    migrated_keys: tuple[str, ...] = ()
    backup_path: Path | None = None


def migrate_v2_document(document: dict[str, Any]) -> ConfigMigrationResult:
    """Return a migrated copy without mutating or writing the source document."""
    if isinstance(document.get("llm"), dict):
        return ConfigMigrationResult(False, deepcopy(document))
    result = deepcopy(document)
    model = result.pop("model", {})
    if not isinstance(model, dict):
        raise ValueError("legacy model section must be a JSON object")
    wire_api = str(model.get("wire_api", "chat_completions"))
    provider: dict[str, Any] = {
        "driver": "openai",
        "base_url": model.get("base_url", "https://api.openai.com/v1"),
        "credential": "openai",
        "api_key_env": "OPENAI_API_KEY",
    }
    user_agent = str(model.get("user_agent") or "").strip()
    if user_agent:
        provider["headers"] = {"User-Agent": user_agent}
    result["llm"] = {
        "providers": {"openai": provider},
        "models": {
            "primary": {
                "provider": "openai",
                "model": str(model.get("model") or "gpt-4o-mini"),
                "api": (
                    "openai_responses" if wire_api == "responses" else "openai_chat"
                ),
                "context_window": int(model.get("context_window", 128_000)),
                "max_output_tokens": int(model.get("max_tokens", 4_096)),
                "capabilities": {
                    "tools": True,
                    "vision": True,
                    "reasoning": True,
                    "structured_output": True,
                },
                "defaults": {
                    key: model[key]
                    for key in (
                        "temperature",
                        "top_p",
                        "thinking_level",
                        "thinking_budget",
                        "service_tier",
                    )
                    if key in model
                },
            }
        },
        "roles": {
            "default": "primary",
            "reasoning": "primary",
            "fast": "primary",
            "vision": "primary",
        },
        "max_retries": int(model.get("retry_count", 2)),
    }
    secrets = result.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        raise ValueError("secrets section must be a JSON object")
    legacy_key = secrets.pop("openai_api_key", None)
    if legacy_key:
        llm_secrets = secrets.setdefault("llm", {})
        if not isinstance(llm_secrets, dict):
            raise ValueError("secrets.llm must be a JSON object")
        llm_secrets.setdefault("openai", {"api_key": legacy_key})
    result["version"] = "3.0.0"
    return ConfigMigrationResult(
        True,
        result,
        migrated_keys=("model→llm", "secrets.openai_api_key→secrets.llm.openai"),
    )


def migrate_config_file(path: Path, *, write: bool) -> ConfigMigrationResult:
    """Validate and optionally write one migration with a timestamped backup."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("config root must be a JSON object")
    migration = migrate_v2_document(document)
    if not write or not migration.changed:
        return migration
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.v2.{stamp}.bak")
    shutil.copy2(path, backup)
    atomic_dump_json(path, migration.document, indent=2, ensure_ascii=False)
    return ConfigMigrationResult(
        migration.changed,
        migration.document,
        migration.migrated_keys,
        backup,
    )


__all__ = ["ConfigMigrationResult", "migrate_config_file", "migrate_v2_document"]
