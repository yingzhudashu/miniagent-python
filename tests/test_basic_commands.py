"""独立基础命令处理器的渠道与错误映射契约。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from miniagent.engine.commands.basic_commands import (
    handle_config,
    handle_doctor,
    handle_help,
    handle_model,
    handle_reload_config,
    handle_schedule,
    handle_stats,
)


@pytest.mark.asyncio
async def test_model_config_and_doctor_capture_results() -> None:
    with (
        patch("miniagent.engine.model_cmd.format_model_info", return_value="model-info"),
        patch("miniagent.engine.model_cmd.switch_model", return_value="switched") as switch,
        patch("miniagent.engine.config_cmd.format_config_info", return_value="config") as config,
        patch("miniagent.engine.doctor.diagnose_environment", return_value="healthy"),
    ):
        assert await handle_model("/model", capture=True) == "model-info"
        assert await handle_model("/model gpt-test", capture=True) == "switched"
        assert await handle_config("/config memory", capture=True) == "config"
        assert await handle_doctor("/doctor", capture=True) == "healthy"
    switch.assert_called_once_with("gpt-test")
    config.assert_called_once_with("memory")


@pytest.mark.asyncio
async def test_print_channel_emits_once(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("miniagent.engine.model_cmd.format_model_info", return_value="model-info"):
        assert await handle_model("/model", capture=False) is None
    assert capsys.readouterr().out.strip() == "model-info"


@pytest.mark.asyncio
async def test_help_and_stats_degrade_without_runtime_or_monitor() -> None:
    assert "未初始化" in (await handle_help("/help", state={}, capture=True) or "")
    assert "未初始化" in (await handle_stats("/stats", capture=True) or "")
    monitor = SimpleNamespace(report=lambda: "requests=3")
    assert await handle_stats("/stats", monitor=monitor, capture=True) == "requests=3"


@pytest.mark.asyncio
async def test_help_captures_builtin_print_contract() -> None:
    runtime = SimpleNamespace(message_queue=object())
    with patch("miniagent.engine.cli_commands.cmd_help", side_effect=lambda *_: print("help")):
        assert await handle_help("/help", state={"runtime_ctx": runtime}, capture=True) == "help"


@pytest.mark.asyncio
async def test_schedule_forwards_remote_mutation_policy() -> None:
    with (
        patch("miniagent.engine.cli_commands.cmd_schedule", return_value="schedule") as command,
        patch("miniagent.engine.cli_commands.feishu_dot_commands_full_enabled", return_value=False),
    ):
        result = await handle_schedule(
            "/schedule add daily",
            capture=True,
            allow_session_mutations_when_capture=False,
        )
    assert result == "schedule"
    command.assert_called_once_with("/schedule add daily", allow_mutations=False)


@pytest.mark.asyncio
async def test_reload_config_maps_success_failure_and_missing_runtime() -> None:
    assert "未初始化" in (
        await handle_reload_config("/reload-config", state={}, capture=True) or ""
    )
    runtime = object()
    reload_mock = AsyncMock()
    with patch("miniagent.infrastructure.json_config.reload_runtime_config", reload_mock):
        assert "重新加载" in (
            await handle_reload_config(
                "/reload-config", state={"runtime_ctx": runtime}, capture=True
            )
            or ""
        )
    reload_mock.assert_awaited_once_with(runtime)

    reload_mock = AsyncMock(side_effect=ValueError("bad config"))
    with patch("miniagent.infrastructure.json_config.reload_runtime_config", reload_mock):
        assert "bad config" in (
            await handle_reload_config(
                "/reload-config", state={"runtime_ctx": runtime}, capture=True
            )
            or ""
        )
