"""thinking_callback 与分步思考 header 形状的单元测试。"""

from __future__ import annotations

import inspect

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


@pytest.mark.asyncio
async def test_invoke_on_thinking_passes_reset_and_is_last_step() -> None:
    received: list[dict[str, object]] = []

    async def cb(
        text: str,
        streaming: bool,
        header: str,
        *,
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
async def test_invoke_on_thinking_falls_back_to_three_arg_on_type_error() -> None:
    calls: list[tuple[object, ...]] = []

    async def _signature_donor(
        text: str, streaming: bool, header: str, *, full_record: str | None = None
    ) -> None:
        pass

    async def cb(text: str, streaming: bool, header: str) -> None:
        calls.append((text, streaming, header))

    cb.__signature__ = inspect.signature(_signature_donor)  # type: ignore[attr-defined]

    await invoke_on_thinking(cb, "x", True, "y", full_record="FULL")
    assert calls == [("x", True, "y")]


@pytest.mark.asyncio
async def test_invoke_on_thinking_swallows_callback_exceptions() -> None:
    async def cb(text: str, streaming: bool, header: str) -> None:
        raise RuntimeError("boom")

    await invoke_on_thinking(cb, "t", True, "h")


@pytest.mark.asyncio
async def test_signature_cache_invalidates_after_callback_gc() -> None:
    """回归：旧版 id(cb) 缓存在对象 GC 后可能被新回调误命中。"""
    import gc

    received: list[bool] = []

    async def three_arg(text: str, streaming: bool, header: str) -> None:
        pass

    await invoke_on_thinking(three_arg, "t", True, "h", reset=True)
    del three_arg
    gc.collect()

    async def with_reset(
        text: str, streaming: bool, header: str, *, reset: bool = False
    ) -> None:
        received.append(reset)

    await invoke_on_thinking(with_reset, "t", True, "h", reset=True)
    assert received == [True]


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
