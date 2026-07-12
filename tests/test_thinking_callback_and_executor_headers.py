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
        text: str,
        streaming: bool,
        header: str,
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None:
        received.append(full_record)

    await invoke_on_thinking(cb, "d", False, "h", full_record="FULL")
    assert received == ["FULL"]


@pytest.mark.asyncio
async def test_invoke_on_thinking_passes_reset_and_is_last_step() -> None:
    received: list[dict[str, object]] = []

    async def cb(
        text: str,
        streaming: bool,
        header: str,
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None:
        received.append({"reset": reset, "is_last_step": is_last_step})

    await invoke_on_thinking(cb, "t", True, "h", reset=True, is_last_step=True)
    assert received == [{"reset": True, "is_last_step": True}]


@pytest.mark.asyncio
async def test_invoke_on_thinking_passes_reset_via_var_keyword() -> None:
    received: list[object] = []

    async def cb(text: str, streaming: bool, header: str, **kwargs: object) -> None:
        received.append(kwargs.get("reset"))

    await invoke_on_thinking(cb, "t", False, "h", reset=True)
    assert received == [True]


@pytest.mark.asyncio
async def test_invoke_on_thinking_swallows_callback_exceptions() -> None:
    async def cb(text: str, streaming: bool, header: str, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    await invoke_on_thinking(cb, "t", True, "h")


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
