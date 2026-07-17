"""Static contracts between production emitters and the Trace event registry."""

from __future__ import annotations

import ast
from pathlib import Path

from miniagent.agent.trace_events import TRACE_EVENT_TYPES

ROOT = Path(__file__).resolve().parent.parent.parent


def test_literal_production_trace_types_are_registered() -> None:
    emitted: dict[str, list[str]] = {}
    for path in (ROOT / "miniagent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            target = call.func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if name != "emit_trace" or not call.args or not isinstance(call.args[0], ast.Dict):
                continue
            for key, value in zip(call.args[0].keys, call.args[0].values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "type"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    emitted.setdefault(value.value, []).append(
                        f"{path.relative_to(ROOT).as_posix()}:{call.lineno}"
                    )

    unknown = {name: locations for name, locations in emitted.items() if name not in TRACE_EVENT_TYPES}
    assert unknown == {}
