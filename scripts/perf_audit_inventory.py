#!/usr/bin/env python3
"""Create a deterministic, line-addressed review inventory for the repository."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileAudit:
    path: str
    lines: int
    python: bool
    functions: int
    classes: int
    imports: int
    exception_handlers: int
    broad_exception_lines: tuple[int, ...]
    blocking_io_lines: tuple[int, ...]
    json_serialization_lines: tuple[int, ...]
    review_status: str


def _tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item for item in result.stdout.decode().split("\0") if item]


def _python_audit(path: Path, root: Path) -> FileAudit:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    broad: list[int] = []
    blocking: list[int] = []
    serialization: list[int] = []
    imports = 0
    handlers = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            imports += 1
        elif isinstance(node, ast.ExceptHandler):
            handlers += 1
            if node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
            ):
                broad.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"sleep", "read_text", "write_text", "read_bytes", "write_bytes"}:
                blocking.append(node.lineno)
            if node.func.attr in {"dumps", "loads", "dump", "load"}:
                serialization.append(node.lineno)
    return FileAudit(
        path=str(path.relative_to(root)).replace("\\", "/"),
        lines=len(text.splitlines()),
        python=True,
        functions=sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree)),
        classes=sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree)),
        imports=imports,
        exception_handlers=handlers,
        broad_exception_lines=tuple(sorted(broad)),
        blocking_io_lines=tuple(sorted(blocking)),
        json_serialization_lines=tuple(sorted(serialization)),
        review_status="pending",
    )


def inventory(root: Path) -> list[FileAudit]:
    result: list[FileAudit] = []
    for path in _tracked_files(root):
        if not path.is_file():
            continue
        if path.suffix == ".py":
            result.append(_python_audit(path, root))
        else:
            result.append(
                FileAudit(
                    path=str(path.relative_to(root)).replace("\\", "/"),
                    lines=len(path.read_text(encoding="utf-8", errors="replace").splitlines()),
                    python=False,
                    functions=0,
                    classes=0,
                    imports=0,
                    exception_handlers=0,
                    broad_exception_lines=(),
                    blocking_io_lines=(),
                    json_serialization_lines=(),
                    review_status="pending",
                )
            )
    return sorted(result, key=lambda item: item.path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json-out", default="workspaces/logs/perf-audit/inventory.json")
    parser.add_argument("--markdown-out", default="workspaces/logs/perf-audit/inventory.md")
    args = parser.parse_args()
    root = args.root.resolve()
    records = inventory(root)
    payload = {
        "schema_version": 1,
        "tracked_file_count": len(records),
        "python_file_count": sum(record.python for record in records),
        "records": [asdict(record) for record in records],
    }
    json_path = root / args.json_out
    markdown_path = root / args.markdown_out
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Performance Audit Inventory", "", f"Tracked files: {len(records)}", "", "| File | Lines | Python | Functions | Classes | Review |", "|---|---:|:---:|---:|---:|---|"]
    lines.extend(
        f"| `{record.path}` | {record.lines} | {'yes' if record.python else 'no'} | {record.functions} | {record.classes} | {record.review_status} |"
        for record in records
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"tracked_file_count": len(records), "json": str(json_path), "markdown": str(markdown_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
