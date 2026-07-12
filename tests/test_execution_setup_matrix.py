"""执行工具选择与步骤标题纯函数的边界矩阵。"""

from __future__ import annotations

from types import SimpleNamespace

from miniagent.core.execution_setup import _resolve_exec_tools, _step_thinking_header


class _Registry:
    def __init__(self, *, core: bool = True) -> None:
        self.core = core

    def get_schemas(self):
        return ["all"]

    def get_schemas_by_toolboxes(self, names):
        return [f"boxes:{','.join(names or [])}"]

    def get_all(self):
        toolbox = None if self.core else "extra"
        return {"tool": SimpleNamespace(schema="core", toolbox=toolbox)}


def _plan(*, enabled=True, toolboxes=None):
    return SimpleNamespace(tools_enabled=enabled, required_toolboxes=toolboxes)


def _config(strategy: str):
    return SimpleNamespace(tool_selection_strategy=strategy)


def test_resolve_exec_tools_obeys_strategy_precedence() -> None:
    registry = _Registry()
    assert _resolve_exec_tools(registry, _config("all"), _plan(enabled=False), None) == []
    assert _resolve_exec_tools(registry, _config("all"), _plan(), None) == ["all"]
    assert _resolve_exec_tools(
        registry,
        _config("auto"),
        _plan(toolboxes=["plan"]),
        SimpleNamespace(required_toolboxes=["step"]),
    ) == ["boxes:step"]
    assert _resolve_exec_tools(registry, _config("auto"), _plan(), None) == ["core"]
    assert _resolve_exec_tools(_Registry(core=False), _config("auto"), _plan(), None) == ["all"]
    assert _resolve_exec_tools(
        registry,
        _config("manual"),
        _plan(toolboxes=["manual"]),
        None,
    ) == ["boxes:manual"]


def test_step_thinking_header_uses_fallback_and_truncates_description() -> None:
    assert (
        _step_thinking_header(
            1, 3, SimpleNamespace(step_number=None, description="line one\nline two")
        )
        == "[步骤 2/3] line one line two"
    )
    header = _step_thinking_header(0, 1, SimpleNamespace(step_number=7, description="x" * 80))
    assert header.startswith("[步骤 7/1]")
    assert header.endswith("…")
