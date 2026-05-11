"""thinking_callback 与分步思考 header 形状的单元测试。"""

from __future__ import annotations

import pytest

from miniagent.core.executor import _step_thinking_header
from miniagent.core.thinking_callback import invoke_on_thinking
from miniagent.types.planning import PlanStep


@pytest.mark.asyncio
async def test_invoke_on_thinking_passes_full_record_with_var_keyword() -> None:
    received: list[object] = []

    async def cb(text: str, streaming: bool, header: str, **kwargs: object) -> None:
        received.append(kwargs.get("full_record"))

    await invoke_on_thinking(cb, "d", True, "h", full_record="FULL")
    assert received == ["FULL"]


@pytest.mark.asyncio
async def test_invoke_on_thinking_passes_full_record_named_param() -> None:
    received: list[str | None] = []

    async def cb(
        text: str, streaming: bool, header: str, *, full_record: str | None = None
    ) -> None:
        received.append(full_record)

    await invoke_on_thinking(cb, "d", False, "h", full_record="FULL")
    assert received == ["FULL"]


@pytest.mark.asyncio
async def test_invoke_on_thinking_three_arg_cb_ignores_full_record_without_error() -> None:
    calls: list[tuple[object, ...]] = []

    async def cb(text: str, streaming: bool, header: str) -> None:
        calls.append((text, streaming, header))

    await invoke_on_thinking(cb, "x", True, "y", full_record=None)
    assert calls == [("x", True, "y")]
    await invoke_on_thinking(cb, "a", False, "b", full_record="should_not_break")
    assert calls == [("x", True, "y"), ("a", False, "b")]


def test_step_thinking_header_shape_and_truncation() -> None:
    long_desc = "字" * 80
    step = PlanStep(
        step_number=2,
        description=long_desc,
        required_toolboxes=[],
    )
    h = _step_thinking_header(0, 5, step)
    assert h.startswith("[步骤 2/5]")
    assert len(h) < len("[步骤 2/5] " + long_desc)
