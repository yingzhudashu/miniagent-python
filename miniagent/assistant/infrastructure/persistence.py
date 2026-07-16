"""Strict JSON state schemas for the current MiniAgent release."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from miniagent.assistant.infrastructure.atomic_json import atomic_dump_json

JsonObject = dict[str, Any]


class StateSchemaError(ValueError):
    """A state document does not match the current registered schema."""


@dataclass(frozen=True, slots=True)
class StateSchema:
    """Name and exact version of one current JSON state document."""

    name: str
    current_version: int

    def validate(self, payload: object) -> JsonObject:
        """Return a detached current document or raise without mutating input."""
        if not isinstance(payload, Mapping):
            raise StateSchemaError(f"{self.name} 状态顶层必须是 JSON 对象")
        document = dict(payload)
        version = document.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise StateSchemaError(
                f"{self.name} schema_version 必须是整数 {self.current_version}"
            )
        if version != self.current_version:
            raise StateSchemaError(
                f"{self.name} schema_version 必须是 {self.current_version}，实际为 {version}"
            )
        return document


_SCHEMAS: dict[str, StateSchema] = {}


def register_state_schema(schema: StateSchema) -> None:
    """Register one current schema; duplicate names are rejected."""
    if schema.current_version < 1:
        raise ValueError("current_version 必须大于等于 1")
    if schema.name in _SCHEMAS:
        raise ValueError(f"状态 schema 已注册: {schema.name}")
    _SCHEMAS[schema.name] = schema


def get_state_schema(name: str) -> StateSchema:
    """Return a registered current schema."""
    try:
        return _SCHEMAS[name]
    except KeyError as error:
        raise StateSchemaError(f"未知状态 schema: {name}") from error


def validate_state(name: str, payload: object) -> JsonObject:
    """Validate an in-memory document against the exact current schema."""
    return get_state_schema(name).validate(payload)


def load_state_file(name: str, path: str | Path) -> JsonObject:
    """Read a current state file without rewriting, backing up, or migrating it."""
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8-sig"))
    return validate_state(name, raw)


def dump_state_file(
    name: str,
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
) -> None:
    """Stamp and atomically write one document in the current schema."""
    schema = get_state_schema(name)
    document = dict(payload)
    document.pop("version", None)
    document["schema_version"] = schema.current_version
    validated = schema.validate(document)
    atomic_dump_json(path, validated, ensure_ascii=ensure_ascii, indent=indent)


__all__ = [
    "StateSchema",
    "StateSchemaError",
    "dump_state_file",
    "get_state_schema",
    "load_state_file",
    "register_state_schema",
    "validate_state",
]
