"""Reject pre-3.0 compatibility and known duplicate runtime designs."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "miniagent"
ALLOWED_TOP = {"llm", "agent", "ui", "assistant"}
FORBIDDEN_MODULE_PARTS = {
    "legacy",
    "migration",
    "request_payload.py",
    "metrics.py",
    "qwen_extra.py",
}
FORBIDDEN_SOURCE = {
    "secrets.openai_api_key",
    "get_default_llm_overrides",
    "_miniagent_llm_gateway",
    "resolve_wire_api",
    "resolve_model_max_output_tokens",
    "TuiViewState",
    "_TuiApplication",
    "PerformanceMetrics",
    "invoke_activity_log",
    "send_ordered",
}
UNIQUE_FUNCTIONS = {
    "escape_markdown_cell",
    "build_interactive_card",
}


def check_current_version(package: Path = PACKAGE) -> list[str]:
    errors: list[str] = []
    actual_top = {path.name for path in package.iterdir() if path.is_dir() and path.name != "__pycache__"}
    if actual_top != ALLOWED_TOP:
        errors.append(f"top-level packages must be {sorted(ALLOWED_TOP)}, got {sorted(actual_top)}")

    function_counts: Counter[str] = Counter()
    for path in package.rglob("*.py"):
        relative = path.relative_to(package).as_posix()
        lowered_parts = {part.lower() for part in path.parts}
        if any(marker in relative.lower() or marker in lowered_parts for marker in FORBIDDEN_MODULE_PARTS):
            errors.append(f"forbidden compatibility/test-only module: {relative}")
        source = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_SOURCE:
            if marker in source:
                errors.append(f"forbidden runtime marker {marker!r}: {relative}")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            errors.append(f"syntax error in {relative}: {error}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_counts[node.name] += 1

    for name in UNIQUE_FUNCTIONS:
        if function_counts[name] != 1:
            errors.append(f"{name} must have one owner, found {function_counts[name]}")

    production_tui = package / "assistant" / "engine" / "cli_tui_app.py"
    if "class AssistantTuiApplication(TuiApp)" not in production_tui.read_text(encoding="utf-8"):
        errors.append("production TUI must use miniagent.ui.TuiApp")
    return errors


def main() -> int:
    errors = check_current_version()
    if errors:
        print("Current-version purity check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Current-version purity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
