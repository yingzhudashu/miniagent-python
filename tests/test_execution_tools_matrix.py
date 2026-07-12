"""Direct contracts for the split tool-call phase runner."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core import execution_tools
from miniagent.core.execution_tools import ToolPhaseRunner
from miniagent.types.config import AgentConfig
from miniagent.types.confirmation import ConfirmationResult
from miniagent.types.tool import ToolResult


def _tool_call(name: str, arguments: str = "{}", call_id: str = "call") -> object:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _runner(*, tools=None, loop_level="ok", on_finish=None, on_call=None) -> ToolPhaseRunner:
    context = SimpleNamespace(messages=[], append=lambda msg: context.messages.append(msg))
    registry = SimpleNamespace(
        get=lambda name: (tools or {}).get(name),
        list=lambda: list((tools or {}).keys()),
    )
    loop = SimpleNamespace(
        check=lambda *_args: SimpleNamespace(level=loop_level, message="loop detected"),
        record=MagicMock(),
    )
    return ToolPhaseRunner(
        context_manager=context,
        agent_config=AgentConfig(tool_timeout=1, allow_parallel_tools=False),
        effective_registry=registry,
        session_key="session",
        on_tool_call=on_call,
        loop_detector=loop,
        monitor=SimpleNamespace(record=MagicMock()),
        turn_tool_calls=[],
        activity_log_enabled=False,
        activity_log=None,
        confirmation_channel=None,
        on_thinking=None,
        tool_context=SimpleNamespace(),
        execution_semaphore=asyncio.Semaphore(2),
        on_tool_finish=on_finish,
    )


@pytest.mark.asyncio
async def test_tool_confirmation_gate_matrix() -> None:
    config = AgentConfig(auto_execute_confirmed=False)
    assert await execution_tools._await_tool_confirmation(
        tool_name="safe",
        help_text="help",
        args={},
        permission="sandbox",
        confirmation_channel=None,
        agent_config=config,
        on_thinking=None,
        thinking_header="step",
    ) is None
    config.auto_execute_confirmed = True
    assert await execution_tools._await_tool_confirmation(
        tool_name="write",
        help_text="help",
        args={},
        permission="require-confirm",
        confirmation_channel=None,
        agent_config=config,
        on_thinking=None,
        thinking_header="step",
    ) is None
    config.auto_execute_confirmed = False
    missing = await execution_tools._await_tool_confirmation(
        tool_name="write",
        help_text="help",
        args={},
        permission="require-confirm",
        confirmation_channel=None,
        agent_config=config,
        on_thinking=None,
        thinking_header="step",
    )
    assert missing is not None and missing.meta["error_type"] == "ConfirmationRequired"

    thinking = AsyncMock()
    channel = SimpleNamespace(
        request_confirmation=AsyncMock(return_value=ConfirmationResult.reject())
    )
    denied = await execution_tools._await_tool_confirmation(
        tool_name="write",
        help_text="help",
        args={"large": "x" * 600},
        permission="require-confirm",
        confirmation_channel=channel,
        agent_config=config,
        on_thinking=thinking,
        thinking_header="step",
    )
    assert denied is not None and denied.meta["error_type"] == "ConfirmationRejected"
    assert thinking.await_count == 1
    channel.request_confirmation.return_value = ConfirmationResult.confirm()
    assert await execution_tools._await_tool_confirmation(
        tool_name="write",
        help_text="help",
        args={},
        permission="require-confirm",
        confirmation_channel=channel,
        agent_config=config,
        on_thinking=None,
        thinking_header="step",
    ) is None


def test_argument_truncation_and_error_logging(monkeypatch) -> None:
    assert execution_tools._truncate_args_for_log("short", 10) == "short"
    assert execution_tools._truncate_args_for_log("long-value", 4).endswith("...[截断]")
    assert execution_tools._truncate_args_for_log({"a": 1}, 20) == '{"a": 1}'
    assert execution_tools._truncate_args_for_log({"a": "long"}, 4).endswith("...[截断]")
    emitted: list[dict] = []
    monkeypatch.setattr(execution_tools, "emit_trace", emitted.append)
    monkeypatch.setattr(execution_tools._logger, "warning", MagicMock())
    monkeypatch.setattr(execution_tools._logger, "error", MagicMock())
    monkeypatch.setattr(execution_tools._logger, "debug", MagicMock())
    execution_tools._log_tool_error(
        tool_name="tool",
        tool_call_id="call",
        args={},
        session_key="session",
        error_type="ValueError",
        error_message="bad",
        is_user_error=True,
    )
    execution_tools._log_tool_error(
        tool_name="tool",
        tool_call_id=None,
        args={},
        session_key=None,
        error_type="RuntimeError",
        error_message="bad",
        traceback_str="trace",
    )
    assert len(emitted) == 2


@pytest.mark.asyncio
async def test_unknown_tool_invalid_json_and_context_short_circuit(monkeypatch) -> None:
    finish = AsyncMock()
    call = MagicMock()
    runner = _runner(on_finish=finish, on_call=call)
    monkeypatch.setattr(execution_tools, "_log_tool_error", MagicMock())
    message = SimpleNamespace(
        content="thinking",
        tool_calls=[_tool_call("missing", "not-json")],
    )
    assert await runner.run_tool_calls_phase(message, 0, "step") is None
    assert runner.context_manager.messages[-1]["role"] == "tool"
    call.assert_called_once()
    finish.assert_awaited_once()

    monkeypatch.setattr(execution_tools, "_append_context_or_return", lambda *_args: "overflow")
    assert await runner.run_tool_calls_phase(message, 0, "step") == "overflow"


@pytest.mark.asyncio
async def test_loop_warning_critical_and_parse_failure() -> None:
    tool = SimpleNamespace(handler=AsyncMock(return_value=ToolResult(True, "ok")), permission="sandbox")
    critical = _runner(tools={"tool": tool}, loop_level="critical")
    result = await critical.run_tool_calls_phase(
        SimpleNamespace(content="", tool_calls=[_tool_call("tool")]), 0, "step"
    )
    assert "loop detected" in result
    critical.monitor.record.assert_called_once()

    warning = _runner(tools={"tool": tool}, loop_level="warning")
    await warning.run_tool_calls_phase(
        SimpleNamespace(content="", tool_calls=[_tool_call("tool", "not-json")]), 0, "step"
    )
    assert warning.loop_warning_shown is False
    assert warning.turn_tool_calls[-1]["result"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (PermissionError("denied"), "PermissionError"),
        (FileNotFoundError("missing"), "FileNotFoundError"),
        (ValueError("bad value"), "ValueError"),
        (RuntimeError("broken"), "RuntimeError"),
    ],
)
async def test_tool_execution_exception_mapping(monkeypatch, error, error_type) -> None:
    async def fail(*_args):
        raise error

    tool = SimpleNamespace(handler=fail, permission="sandbox", help_text="")
    runner = _runner(tools={"tool": tool})
    logs: list[dict] = []
    monkeypatch.setattr(execution_tools, "_log_tool_error", lambda **kwargs: logs.append(kwargs))
    result = await runner.run_tool_calls_phase(
        SimpleNamespace(content="", tool_calls=[_tool_call("tool")]), 0, "step"
    )
    assert result is None
    assert runner.turn_tool_calls[-1]["result"]
    assert logs[-1]["error_type"] == error_type


@pytest.mark.asyncio
async def test_tool_timeout_parallel_and_finish_callback_failure(monkeypatch) -> None:
    async def slow(*_args):
        await asyncio.sleep(10)

    slow_tool = SimpleNamespace(handler=slow, permission="sandbox", help_text="")
    fast_tool = SimpleNamespace(
        handler=AsyncMock(return_value=ToolResult(True, "fast", {"input_bytes": 2})),
        permission="sandbox",
        help_text="",
    )
    finish = AsyncMock(side_effect=RuntimeError("finish failed"))
    runner = _runner(tools={"slow": slow_tool, "fast": fast_tool}, on_finish=finish)
    runner.agent_config.tool_timeout = 0
    runner.agent_config.allow_parallel_tools = True
    runner.agent_config.debug = True
    monkeypatch.setattr(execution_tools, "_log_tool_error", MagicMock())
    await runner.run_tool_calls_phase(
        SimpleNamespace(
            content="",
            tool_calls=[_tool_call("slow", call_id="slow"), _tool_call("fast", call_id="fast")],
        ),
        0,
        "step",
    )
    assert len(runner.turn_tool_calls) == 2
    assert any("超时" in item["result"] for item in runner.turn_tool_calls)
    assert finish.await_count == 2

