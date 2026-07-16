#!/usr/bin/env python3
"""Enforce MiniAgent's four-module topology with a complete AST scan."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

LAYERS = ("llm", "agent", "ui", "assistant")
ALLOWED_DEPENDENCIES: dict[str, frozenset[str]] = {
    "llm": frozenset({"llm"}),
    "agent": frozenset({"agent", "llm"}),
    "ui": frozenset({"ui"}),
    "assistant": frozenset({"assistant", "agent", "llm", "ui"}),
    "__root__": frozenset({"assistant"}),
}
MAX_FUNCTION_LINES = 100


@dataclass(frozen=True, slots=True)
class DependencyRule:
    """Compatibility hook for callers that want to disable dependency checks."""

    source_package: str
    forbidden_prefixes: tuple[str, ...]


DEFAULT_RULES = tuple(
    DependencyRule(layer, ()) for layer in LAYERS
)


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    source_package: str
    imported_module: str

    def format(self, root: Path) -> str:
        return _display(self.path, root) + (
            f":{self.line}: {self.source_package} must not import {self.imported_module}"
        )


@dataclass(frozen=True, slots=True)
class TopologyViolation:
    path: Path
    package: str

    def format(self, root: Path) -> str:
        return f"{_display(self.path, root)}: unexpected top-level package {self.package!r}"


@dataclass(frozen=True, slots=True)
class CycleViolation:
    cycle: tuple[str, ...]

    def format(self, root: Path) -> str:
        del root
        return "cross-layer dependency cycle: " + " -> ".join(self.cycle)


@dataclass(frozen=True, slots=True)
class FunctionLengthViolation:
    path: Path
    line: int
    function_name: str
    length: int
    limit: int

    def format(self, root: Path) -> str:
        return _display(self.path, root) + (
            f":{self.line}: function {self.function_name} has {self.length} lines "
            f"(limit {self.limit})"
        )


@dataclass(frozen=True, slots=True)
class ModuleDependencyViolation:
    path: Path
    line: int
    message: str

    def format(self, root: Path) -> str:
        return f"{_display(self.path, root)}:{self.line}: {self.message}"


ArchitectureViolation = (
    Violation
    | TopologyViolation
    | CycleViolation
    | FunctionLengthViolation
    | ModuleDependencyViolation
)


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root.parent))
    except ValueError:
        return str(path)


def _module_parts(package_root: Path, path: Path) -> tuple[str, ...]:
    relative = path.relative_to(package_root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ("miniagent", *parts)


def _absolute_imports(
    package_root: Path,
    path: Path,
    node: ast.Import | ast.ImportFrom,
) -> Iterator[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    if node.level == 0:
        if node.module:
            yield node.module
        return
    module = _module_parts(package_root, path)
    package = module if path.name == "__init__.py" else module[:-1]
    trim = node.level - 1
    base = package[: max(0, len(package) - trim)]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    resolved = ".".join((*base, *suffix))
    if resolved:
        yield resolved
    elif not node.module:
        for alias in node.names:
            yield ".".join((*base, alias.name))


def _source_layer(package_root: Path, path: Path) -> str:
    relative = path.relative_to(package_root)
    return relative.parts[0] if len(relative.parts) > 1 else "__root__"


def _target_layer(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "miniagent":
        return None
    return parts[1] if parts[1] in LAYERS else "__unknown__"


def _check_internal_dependency(
    package_root: Path,
    path: Path,
    node: ast.Import | ast.ImportFrom,
) -> ModuleDependencyViolation | None:
    relative = path.relative_to(package_root)
    imported = tuple(_absolute_imports(package_root, path, node))
    if (
        relative.parts[:3] == ("assistant", "engine", "commands")
        and "miniagent.assistant.engine.command_dispatch" in imported
    ):
        return ModuleDependencyViolation(
            path,
            node.lineno,
            "command modules must not import the command dispatcher",
        )
    if relative.parts[:2] == ("assistant", "testing"):
        return None
    if (
        relative.parts[:1] == ("assistant",)
        and isinstance(node, ast.ImportFrom)
        and "miniagent.agent.agent" in imported
        and any(alias.name == "run_agent" for alias in node.names)
    ):
        return ModuleDependencyViolation(
            path,
            node.lineno,
            "assistant production code must invoke the object-oriented Agent facade",
        )
    return None


def _internal_dependency_violations(
    package_root: Path,
    path: Path,
    tree: ast.AST,
) -> Iterator[ModuleDependencyViolation]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        violation = _check_internal_dependency(package_root, path, node)
        if violation is not None:
            yield violation


def _find_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()
    active: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in active:
            start = active.index(node)
            cycle = active[start:] + [node]
            body = cycle[:-1]
            rotations = [tuple(body[i:] + body[:i]) for i in range(len(body))]
            canonical = min(rotations)
            cycles.add((*canonical, canonical[0]))
            return
        if node in visited:
            return
        active.append(node)
        for target in sorted(graph.get(node, ())):
            if target != node:
                visit(target)
        active.pop()
        visited.add(node)

    for layer in LAYERS:
        visit(layer)
    return sorted(cycles)


def check_architecture(
    package_root: Path,
    rules: Iterable[DependencyRule] = DEFAULT_RULES,
) -> list[ArchitectureViolation]:
    """Return topology, dependency, cycle, and function-size violations."""
    dependency_checks = bool(tuple(rules))
    violations: list[ArchitectureViolation] = []
    graph = {layer: set() for layer in LAYERS}

    if dependency_checks:
        for child in sorted(package_root.iterdir()):
            if child.is_dir() and child.name not in LAYERS and child.name != "__pycache__":
                violations.append(TopologyViolation(child, child.name))

    for path in sorted(package_root.rglob("*.py")):
        if "templates" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        source = _source_layer(package_root, path)
        violations.extend(_internal_dependency_violations(package_root, path, tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > MAX_FUNCTION_LINES:
                    violations.append(
                        FunctionLengthViolation(
                            path, node.lineno, node.name, length, MAX_FUNCTION_LINES
                        )
                    )
            if not dependency_checks or not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported in _absolute_imports(package_root, path, node):
                target = _target_layer(imported)
                if target is None:
                    continue
                if source in LAYERS and target in LAYERS:
                    graph[source].add(target)
                if target not in ALLOWED_DEPENDENCIES.get(source, frozenset()):
                    violations.append(Violation(path, node.lineno, source, imported))

    if dependency_checks:
        violations.extend(CycleViolation(cycle) for cycle in _find_cycles(graph))
    return violations


def main(argv: list[str] | None = None) -> int:
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
    print("four-module architecture rules passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
