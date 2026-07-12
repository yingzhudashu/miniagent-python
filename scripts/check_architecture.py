#!/usr/bin/env python3
"""Enforce package dependency boundaries with a lightweight AST scan."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DependencyRule:
    """Forbid runtime imports with selected prefixes below a source package."""

    source_package: str
    forbidden_prefixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Violation:
    """A dependency rule violation with source location and imported module."""

    path: Path
    line: int
    source_package: str
    imported_module: str

    def format(self, root: Path) -> str:
        """Return a stable, CI-friendly error line relative to ``root``."""
        try:
            display = self.path.relative_to(root.parent)
        except ValueError:
            display = self.path
        return (
            f"{display}:{self.line}: {self.source_package} must not import "
            f"{self.imported_module}"
        )


DEFAULT_RULES = (
    DependencyRule(
        "application",
        (
            "miniagent.bootstrap",
            "miniagent.core",
            "miniagent.engine",
            "miniagent.feishu",
            "miniagent.infrastructure",
            "miniagent.knowledge",
            "miniagent.mcp",
            "miniagent.memory",
            "miniagent.runtime",
            "miniagent.scheduled_tasks",
            "miniagent.security",
            "miniagent.session",
            "miniagent.skills",
            "miniagent.testing",
            "miniagent.tools",
            "miniagent.types",
        ),
    ),
    DependencyRule(
        "contracts",
        (
            "miniagent.core",
            "miniagent.engine",
            "miniagent.feishu",
            "miniagent.infrastructure",
            "miniagent.knowledge",
            "miniagent.mcp",
            "miniagent.memory",
            "miniagent.runtime",
            "miniagent.scheduled_tasks",
            "miniagent.security",
            "miniagent.session",
            "miniagent.skills",
            "miniagent.testing",
            "miniagent.tools",
            "miniagent.types",
        ),
    ),
    DependencyRule("types", ("miniagent.feishu",)),
)


def _is_type_checking_guard(node: ast.expr) -> bool:
    """Return whether an if-test is the conventional TYPE_CHECKING guard."""
    return isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"


def _runtime_import_nodes(statements: Iterable[ast.stmt]) -> Iterator[ast.Import | ast.ImportFrom]:
    """Yield imports executed at module load, excluding functions and type guards."""
    for statement in statements:
        if isinstance(statement, ast.Import | ast.ImportFrom):
            yield statement
        elif isinstance(statement, ast.If):
            if not _is_type_checking_guard(statement.test):
                yield from _runtime_import_nodes(statement.body)
                yield from _runtime_import_nodes(statement.orelse)
        elif isinstance(statement, ast.Try):
            yield from _runtime_import_nodes(statement.body)
            for handler in statement.handlers:
                yield from _runtime_import_nodes(handler.body)
            yield from _runtime_import_nodes(statement.orelse)
            yield from _runtime_import_nodes(statement.finalbody)
        elif isinstance(statement, ast.With):
            yield from _runtime_import_nodes(statement.body)


def _imported_modules(node: ast.Import | ast.ImportFrom) -> Iterator[str]:
    """Yield absolute imported module names represented by an AST node."""
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif node.level == 0 and node.module:
        yield node.module


def check_architecture(
    package_root: Path,
    rules: Iterable[DependencyRule] = DEFAULT_RULES,
) -> list[Violation]:
    """Return dependency violations below ``package_root`` without importing code."""
    violations: list[Violation] = []
    for rule in rules:
        source_root = package_root / rule.source_package
        if not source_root.is_dir():
            continue
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in _runtime_import_nodes(tree.body):
                for imported in _imported_modules(node):
                    if imported.startswith(rule.forbidden_prefixes):
                        violations.append(
                            Violation(path, node.lineno, rule.source_package, imported)
                        )
    return violations


def main(argv: list[str] | None = None) -> int:
    """Run the default architecture rules for the repository package."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "miniagent",
        help="miniagent package root",
    )
    args = parser.parse_args(argv)
    violations = check_architecture(args.root)
    for violation in violations:
        print(violation.format(args.root))
    if violations:
        print(f"architecture check failed: {len(violations)} violation(s)")
        return 1
    print("architecture dependency rules passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
