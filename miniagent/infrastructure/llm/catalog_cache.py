"""Atomic last-known-good cache for explicitly refreshed model catalogs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from miniagent.contracts.llm import ModelDescriptor
from miniagent.infrastructure.atomic_json import atomic_dump_json
from miniagent.infrastructure.llm.catalog import model_from_config


def load_catalog_cache(path: Path) -> tuple[ModelDescriptor, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        return ()
    result = []
    for item in document.get("models", ()):
        if not isinstance(item, dict) or not item.get("profile"):
            continue
        try:
            result.append(model_from_config(str(item["profile"]), item))
        except (TypeError, ValueError):
            continue
    return tuple(result)


def _model_data(model: ModelDescriptor) -> dict[str, Any]:
    return {
        "profile": model.profile,
        "provider": model.provider,
        "model": model.model,
        "api": model.api,
        "display_name": model.display_name,
        "context_window": model.context_window,
        "max_output_tokens": model.max_output_tokens,
        "capabilities": {
            "tools": model.capabilities.tools,
            "vision": model.capabilities.vision,
            "reasoning": model.capabilities.reasoning,
            "structured_output": model.capabilities.structured_output,
        },
        "pricing": {
            "input": model.pricing.input,
            "output": model.pricing.output,
            "cache_read": model.pricing.cache_read,
            "cache_write": model.pricing.cache_write,
        },
        "defaults": dict(model.defaults),
        "compatibility": dict(model.compatibility),
    }


def save_catalog_cache(path: Path, models: tuple[ModelDescriptor, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_dump_json(
        path,
        {"schema_version": 1, "models": [_model_data(model) for model in models]},
        indent=2,
        ensure_ascii=False,
    )


__all__ = ["load_catalog_cache", "save_catalog_cache"]
