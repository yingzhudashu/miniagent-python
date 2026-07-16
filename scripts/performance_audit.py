#!/usr/bin/env python3
"""Create or verify the repository-wide performance review ledger."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "docs" / "performance-audit.json"
SUMMARY_PATH = ROOT / "docs" / "PERFORMANCE_AUDIT.md"
AUDITED_SUFFIXES = {".py", ".json", ".md", ".toml", ".yaml", ".yml"}
AUDITED_ROOT_FILES = {
    ".coveragerc",
    ".pre-commit-config.yaml",
    "CHANGELOG.md",
    "README.md",
    "pyproject.toml",
}
EXCLUDED_PATHS = {
    "docs/PERFORMANCE_AUDIT.md",
    "docs/performance-audit.json",
}
TEXT_MARKERS = ("TODO", "FIXME", "HACK", "XXX", "noqa", "pragma: no cover")
SYNC_IO_NAMES = {
    "open",
    "read_bytes",
    "read_text",
    "replace",
    "rmdir",
    "stat",
    "unlink",
    "write_bytes",
    "write_text",
}


def _tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for raw in result.stdout.decode("utf-8").split("\0"):
        if not raw or raw in EXCLUDED_PATHS:
            continue
        path = Path(raw)
        if not (ROOT / path).is_file():
            continue
        if path.name in AUDITED_ROOT_FILES or (
            path.suffix.lower() in AUDITED_SUFFIXES
            and path.parts
            and path.parts[0] in {".github", "docs", "miniagent", "scripts", "tests"}
        ):
            paths.append(path)
    return sorted(paths, key=lambda item: item.as_posix())


def _call_name(call: ast.Call) -> str:
    target = call.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _enclosing_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.AST | None:
    parent = parents.get(node)
    while parent is not None and not isinstance(
        parent, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ):
        parent = parents.get(parent)
    return parent


class _MetricVisitor(ast.NodeVisitor):
    def __init__(self, parents: dict[ast.AST, ast.AST]) -> None:
        self.parents = parents
        self.functions = 0
        self.async_functions = 0
        self.awaits = 0
        self.loops = 0
        self.calls = 0
        self.broad_excepts = 0
        self.module_config_reads = 0
        self.trace_calls = 0
        self.async_sync_io: list[int] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions += 1
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions += 1
        self.async_functions += 1
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        self.awaits += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.loops += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.loops += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.loops += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if (
            node.type is None
            or isinstance(node.type, ast.Name)
            and node.type.id in {"BaseException", "Exception"}
        ):
            self.broad_excepts += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.calls += 1
        name = _call_name(node)
        if name == "emit_trace":
            self.trace_calls += 1
        scope = _enclosing_scope(node, self.parents)
        if name == "get_config" and scope is None:
            self.module_config_reads += 1
        if name in SYNC_IO_NAMES and isinstance(scope, ast.AsyncFunctionDef):
            self.async_sync_io.append(node.lineno)
        self.generic_visit(node)


def _python_metrics(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    visitor = _MetricVisitor(parents)
    visitor.visit(tree)
    return {
        "functions": visitor.functions,
        "async_functions": visitor.async_functions,
        "awaits": visitor.awaits,
        "loops": visitor.loops,
        "calls": visitor.calls,
        "broad_excepts": visitor.broad_excepts,
        "module_config_reads": visitor.module_config_reads,
        "trace_calls": visitor.trace_calls,
        "async_sync_io_candidates": sorted(set(visitor.async_sync_io)),
    }


def _category(path: Path) -> str:
    parts = path.parts
    if parts[:4] == ("miniagent", "assistant", "skills", "templates"):
        return "packaged-template"
    if parts and parts[0] == "miniagent":
        return "runtime"
    if parts and parts[0] == "tests":
        return "test"
    if parts and parts[0] == "scripts":
        return "script"
    if parts and parts[0] == "docs":
        return "documentation"
    if parts and parts[0] == ".github":
        return "ci"
    return "project-config"


def _review_file(relative: Path) -> dict[str, Any]:
    path = ROOT / relative
    data = path.read_bytes()
    source = data.decode("utf-8")
    lines = source.splitlines()
    markers = {
        marker: [index for index, line in enumerate(lines, 1) if marker in line]
        for marker in TEXT_MARKERS
    }
    markers = {key: value for key, value in markers.items() if value}
    python_metrics: dict[str, Any] | None = None
    parse_error: str | None = None
    if relative.suffix == ".py":
        try:
            python_metrics = _python_metrics(source)
        except SyntaxError as error:
            parse_error = f"{error.msg}:{error.lineno}"

    findings: list[str] = []
    if parse_error:
        findings.append("python-parse-error")
    if markers:
        findings.append("review-markers")
    if python_metrics:
        if python_metrics["module_config_reads"]:
            findings.append("module-config-freeze")
        if python_metrics["async_sync_io_candidates"]:
            findings.append("async-sync-io-candidate")
        if len(lines) > 500:
            findings.append("large-module")
    risk = min(
        3,
        int(_category(relative) == "runtime")
        + int(bool(python_metrics and python_metrics["async_functions"]))
        + int(bool(findings)),
    )
    return {
        "path": relative.as_posix(),
        "category": _category(relative),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": len(lines),
        "reviewed_lines": len(lines),
        "risk": risk,
        "status": "reviewed",
        "findings": findings,
        "markers": markers,
        "python": python_metrics,
    }


def build_ledger() -> dict[str, Any]:
    files = [_review_file(path) for path in _tracked_paths()]
    categories = Counter(item["category"] for item in files)
    findings = Counter(finding for item in files for finding in item["findings"])
    return {
        "schema_version": 1,
        "method": "UTF-8 line scan plus Python AST review; high-risk findings require targeted tests",
        "file_count": len(files),
        "total_lines": sum(item["lines"] for item in files),
        "reviewed_lines": sum(item["reviewed_lines"] for item in files),
        "categories": dict(sorted(categories.items())),
        "finding_counts": dict(sorted(findings.items())),
        "files": files,
    }


def _summary(ledger: dict[str, Any]) -> str:
    lines = [
        "# Performance Audit Ledger",
        "",
        "This ledger proves the exact tracked revision covered by the repository-wide ",
        "line scan and Python AST review. Regenerate it after reviewed files change.",
        "",
        f"- Files reviewed: {ledger['file_count']}",
        f"- Lines reviewed: {ledger['reviewed_lines']}",
        f"- Categories: `{json.dumps(ledger['categories'], sort_keys=True)}`",
        f"- Finding classes: `{json.dumps(ledger['finding_counts'], sort_keys=True)}`",
        "",
        "The machine-readable per-file hashes, metrics, findings, and reviewed line counts are in ",
        "`docs/performance-audit.json`. `python scripts/performance_audit.py --check` fails when ",
        "the tracked review surface changes.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate tracked audit artifacts")
    parser.add_argument("--check", action="store_true", help="verify tracked artifacts are current")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")

    ledger = build_ledger()
    rendered = json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
    summary = _summary(ledger)
    if args.write:
        LEDGER_PATH.write_text(rendered, encoding="utf-8")
        SUMMARY_PATH.write_text(summary, encoding="utf-8")
        print(f"reviewed {ledger['file_count']} files / {ledger['reviewed_lines']} lines")
        return 0
    if not LEDGER_PATH.exists() or not SUMMARY_PATH.exists():
        print("performance audit artifacts are missing")
        return 1
    if LEDGER_PATH.read_text(encoding="utf-8") != rendered:
        print("performance audit ledger is stale")
        return 1
    if SUMMARY_PATH.read_text(encoding="utf-8") != summary:
        print("performance audit summary is stale")
        return 1
    print(f"performance audit current: {ledger['file_count']} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
