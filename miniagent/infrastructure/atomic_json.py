"""Same-volume atomic text and JSON persistence helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(
    path: str | os.PathLike[str],
    content: str,
    *,
    encoding: str = "utf-8",
) -> int:
    """Atomically publish text and return the number of encoded bytes."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
        os.replace(temp_path, target)
        temp_path = None
        return target.stat().st_size
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_dump_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    ensure_ascii: bool = True,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    cls: type[json.JSONEncoder] | None = None,
    sort_keys: bool = False,
) -> None:
    """Write a complete sibling file, then atomically publish it as ``path``.

    The caller owns synchronization and retry policy. An unpublished temporary
    file is removed on every failure path.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(
                payload,
                handle,
                ensure_ascii=ensure_ascii,
                indent=indent,
                separators=separators,
                cls=cls,
                sort_keys=sort_keys,
            )
            handle.flush()
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["atomic_dump_json", "atomic_write_text"]
