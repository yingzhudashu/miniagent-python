"""版本化 JSON 文档迁移注册表与显式、安全的落盘迁移。"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from miniagent.infrastructure.atomic_json import atomic_dump_json

JsonObject = dict[str, Any]
Migration = Callable[[JsonObject], JsonObject]


class StateMigrationError(ValueError):
    """状态格式未知、版本过新或迁移步骤不完整。"""


@dataclass(frozen=True, slots=True)
class StateSchema:
    """一种 JSON 状态文档的当前版本及逐版本迁移函数。"""

    name: str
    current_version: int
    migrations: Mapping[int, Migration]
    legacy_version_keys: tuple[str, ...] = ("version",)
    legacy_list_key: str | None = None

    def migrate(self, payload: object) -> JsonObject:
        """返回迁移后的新字典；输入对象始终保持不变。"""
        if isinstance(payload, Mapping):
            document = dict(payload)
        elif isinstance(payload, list) and self.legacy_list_key is not None:
            document = {self.legacy_list_key: list(payload)}
        else:
            raise StateMigrationError(f"{self.name} 状态顶层必须是 JSON 对象")
        version = _document_version(document, self.legacy_version_keys)
        if version > self.current_version:
            raise StateMigrationError(
                f"{self.name} 状态版本 {version} 高于程序支持的 {self.current_version}"
            )
        while version < self.current_version:
            migration = self.migrations.get(version)
            if migration is None:
                raise StateMigrationError(
                    f"{self.name} 缺少 {version} → {version + 1} 迁移步骤"
                )
            migrated = migration(dict(document))
            if not isinstance(migrated, dict):
                raise StateMigrationError(f"{self.name} 迁移 {version} 返回了非对象")
            document = migrated
            version += 1
        document["schema_version"] = self.current_version
        return document


_SCHEMAS: dict[str, StateSchema] = {}


def register_state_schema(schema: StateSchema) -> None:
    """注册状态 schema；同名重复注册会被拒绝。"""
    if schema.current_version < 1:
        raise ValueError("current_version 必须大于等于 1")
    if schema.name in _SCHEMAS:
        raise ValueError(f"状态 schema 已注册: {schema.name}")
    _SCHEMAS[schema.name] = schema


def get_state_schema(name: str) -> StateSchema:
    """获取已注册 schema，未知名称提供明确错误。"""
    try:
        return _SCHEMAS[name]
    except KeyError as error:
        raise StateMigrationError(f"未知状态 schema: {name}") from error


def migrate_state(name: str, payload: object) -> JsonObject:
    """在内存中迁移结构化 JSON 状态，不产生文件副作用。"""
    return get_state_schema(name).migrate(payload)


def migrate_state_file(
    name: str,
    path: str | Path,
    *,
    backup_suffix: str = ".bak",
) -> Path | None:
    """显式迁移文件；有变化时先备份原文件，再原子替换。

    返回备份路径；文件已是当前格式时返回 ``None``。迁移或写入失败时原文件
    保持不变，已创建的备份仍保留以便人工恢复。
    """
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8-sig"))
    migrated = migrate_state(name, raw)
    if migrated == raw:
        return None
    backup = target.with_name(target.name + backup_suffix)
    shutil.copy2(target, backup)
    atomic_dump_json(target, migrated, ensure_ascii=False, indent=2)
    return backup


def load_state_file(name: str, path: str | Path) -> JsonObject:
    """读取、校验并按需原子迁移一个长期状态文件。

    旧格式文件会先复制为同目录 ``.bak``，再以原子替换方式写回当前格式；
    JSON 损坏、顶层类型错误或迁移失败都会抛出可定位的异常，调用方可据其业务
    降级策略决定是否使用空状态，但本函数不会覆盖原文件。
    """
    target = Path(path)
    migrate_state_file(name, target)
    raw = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise StateMigrationError(f"{name} 状态顶层必须是 JSON 对象: {target}")
    return dict(raw)


def dump_state_file(
    name: str,
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
) -> None:
    """验证并原子写入当前版本的长期状态对象。"""
    document = migrate_state(name, payload)
    atomic_dump_json(path, document, ensure_ascii=ensure_ascii, indent=indent)


def _document_version(payload: Mapping[str, Any], legacy_keys: tuple[str, ...]) -> int:
    value: Any = payload.get("schema_version")
    if value is None:
        for key in legacy_keys:
            if key in payload:
                value = payload[key]
                break
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise StateMigrationError(f"schema_version 必须是整数，实际为 {value!r}")
    if value < 0:
        raise StateMigrationError("schema_version 不能为负数")
    return value


__all__ = [
    "StateMigrationError",
    "StateSchema",
    "get_state_schema",
    "dump_state_file",
    "load_state_file",
    "migrate_state",
    "migrate_state_file",
    "register_state_schema",
]
