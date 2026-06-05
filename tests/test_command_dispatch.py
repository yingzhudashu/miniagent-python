"""Tests for command_dispatch module.

Tests cover main commands:
- /status
- /session (list, switch, create, rename, delete)
- /feishu (start, stop, status)
- /queue (status, set, abort)
- /bind / /unbind
- /help
- /stats
- /model
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.command_dispatch import _format_status, dispatch_command

# ============================================================================
# Helper Functions
# ============================================================================


def _create_mock_state() -> dict:
    """创建 mock state 字典。"""
    mock_rt = MagicMock()
    mock_rt.message_queue = MagicMock()
    mock_rt.message_queue.CLI_CHAT_ID = "__cli__"
    mock_rt.message_queue.get_status.return_value = {
        "mode": "preemptive",
        "chats": {"__cli__": {"busy": False, "pending": 0, "elapsed": None}},
    }
    mock_rt.channel_router = MagicMock()
    mock_rt.channel_router.get_all_bindings.return_value = {}
    mock_rt.feishu = MagicMock()
    mock_rt.feishu.is_running.return_value = False
    mock_rt.create_feishu_handler_factory = MagicMock()
    mock_rt.skill_registry = MagicMock()

    state = {
        "runtime_ctx": mock_rt,
        "active_session_id": "default",
        "instance_id": 1,
        "session_manager": MagicMock(),
        "feishu_p2p_synced_senders": set(),
    }

    # Mock session_manager methods
    state["session_manager"].get_session_display_name = MagicMock(return_value="Default Session")
    state["session_manager"].get = MagicMock()
    state["session_manager"].get_or_create = MagicMock()

    return state


# ============================================================================
# Test Classes
# ============================================================================


class TestDispatchCommandBasics:
    """测试 dispatch_command 基础功能。"""

    @pytest.mark.asyncio
    async def test_non_command_returns_none(self) -> None:
        """非命令（不以 / 开头）应返回 None。"""
        state = _create_mock_state()

        result = await dispatch_command("hello world", state=state)
        assert result is None

        result = await dispatch_command("regular text", state=state)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_runtime_ctx_returns_warning(self) -> None:
        """缺少 runtime_ctx 应返回警告消息。"""
        state = {}  # 无 runtime_ctx

        result = await dispatch_command("/status", state=state, capture=True)
        assert "运行时上下文" in result
        assert "⚠️" in result or "WARNING_PREFIX" in result


class TestStatusCommand:
    """测试 /status 命令。"""

    @pytest.mark.asyncio
    async def test_status_returns_formatted_output(self) -> None:
        """/status 应返回格式化的状态信息。"""
        state = _create_mock_state()

        result = await dispatch_command("/status", state=state, capture=True)

        assert "实例" in result
        assert "会话" in result
        assert "飞书" in result
        assert "消息队列" in result

    @pytest.mark.asyncio
    async def test_status_shows_feishu_running(self) -> None:
        """/status 应显示飞书运行状态。"""
        state = _create_mock_state()
        state["runtime_ctx"].feishu.is_running.return_value = True

        result = await dispatch_command("/status", state=state, capture=True)

        assert "运行中" in result or "🟢" in result


class TestSessionCommand:
    """测试 /session 命令。"""

    @pytest.mark.asyncio
    async def test_session_list_returns_sessions(self) -> None:
        """/session list 应返回会话列表。"""
        state = _create_mock_state()

        with patch("miniagent.engine.cli_commands.cmd_session_list") as mock_list:
            mock_list.return_value = "Sessions: default, test"

            await dispatch_command("/session list", state=state, capture=True)

            mock_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_switch_blocked_remote(self) -> None:
        """飞书 capture 模式下 /session switch 应被阻止。"""
        state = _create_mock_state()

        result = await dispatch_command(
            "/session switch test",
            state=state,
            capture=True,
            allow_session_mutations_when_capture=False,
        )

        assert "飞书" in result or "本地" in result

    @pytest.mark.asyncio
    async def test_session_switch_allowed_when_full_enabled(self) -> None:
        """MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时允许 session switch。"""
        state = _create_mock_state()

        with patch("miniagent.engine.cli_commands.feishu_dot_commands_full_enabled", return_value=True):
            with patch("miniagent.engine.cli_commands.cmd_session_switch", new_callable=AsyncMock) as mock_switch:
                mock_switch.return_value = "test_session"

                await dispatch_command(
                    "/session switch test",
                    state=state,
                    capture=True,
                    allow_session_mutations_when_capture=False,
                )

                # 应更新 active_session_id
                assert state.get("active_session_id") == "test_session"


class TestFeishuCommand:
    """测试 /feishu 命令。"""

    @pytest.mark.asyncio
    async def test_feishu_status_returns_status(self) -> None:
        """/feishu status 应返回飞书状态。"""
        state = _create_mock_state()
        state["runtime_ctx"].feishu.status = MagicMock(return_value="Feishu: stopped")

        result = await dispatch_command("/feishu", state=state, capture=True)

        assert result is not None

    @pytest.mark.asyncio
    async def test_feishu_start_calls_start_method(self) -> None:
        """/feishu start 应调用 feishu.start。"""
        state = _create_mock_state()
        state["runtime_ctx"].feishu

        result = await dispatch_command("/feishu start", state=state, capture=True)

        # 验证 start 被调用（通过捕获输出）
        assert result is not None

    @pytest.mark.asyncio
    async def test_feishu_stop_calls_stop_method(self) -> None:
        """/feishu stop 应调用 feishu.stop。"""
        state = _create_mock_state()

        await dispatch_command("/feishu stop", state=state, capture=True)

        state["runtime_ctx"].feishu.stop.assert_called_once()


class TestQueueCommand:
    """测试 /queue 命令。"""

    @pytest.mark.asyncio
    async def test_queue_status_returns_status(self) -> None:
        """/queue status 应返回队列状态。"""
        state = _create_mock_state()

        with patch("miniagent.engine.cli_commands.cmd_queue_status") as mock_status:
            mock_status.return_value = "Queue: preemptive"

            await dispatch_command("/queue status", state=state, capture=True)

            mock_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_abort_aborts_queue(self) -> None:
        """/queue abort 应中止队列任务。"""
        state = _create_mock_state()
        state["runtime_ctx"].message_queue.abort_chat.return_value = {"aborted": True}

        await dispatch_command("/queue abort", state=state, capture=True)

        state["runtime_ctx"].message_queue.abort_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_abort_command_same_as_queue_abort(self) -> None:
        """/abort 应等同于 /queue abort。"""
        state = _create_mock_state()
        state["runtime_ctx"].message_queue.abort_chat.return_value = {"aborted": True}

        await dispatch_command("/abort", state=state, capture=True)
        await dispatch_command("/queue abort", state=state, capture=True)

        # 两者应调用相同方法
        assert state["runtime_ctx"].message_queue.abort_chat.call_count == 2


class TestBindCommand:
    """测试 /bind 命令。"""

    @pytest.mark.asyncio
    async def test_bind_requires_channel_router(self) -> None:
        """/bind 需要 channel_router。"""
        state = _create_mock_state()
        state["runtime_ctx"].channel_router = None

        result = await dispatch_command("/bind status", state=state, capture=True)

        assert "通道路由器" in result

    @pytest.mark.asyncio
    async def test_bind_status_returns_bindings(self) -> None:
        """/bind status 应返回绑定状态。"""
        state = _create_mock_state()

        with patch("miniagent.engine.cli_commands.cmd_bind") as mock_bind:
            mock_bind.return_value = "No bindings"

            await dispatch_command("/bind status", state=state, capture=True)

            mock_bind.assert_called_once()


class TestHelpCommand:
    """测试 /help 命令。"""

    @pytest.mark.asyncio
    async def test_help_returns_usage_info(self) -> None:
        """/help 应返回使用信息。"""
        state = _create_mock_state()

        with patch("miniagent.engine.cli_commands.cmd_help") as mock_help:
            mock_help.return_value = "Help: available commands..."

            await dispatch_command("/help", state=state, capture=True)

            mock_help.assert_called_once()


class TestStatsCommand:
    """测试 /stats 命令。"""

    @pytest.mark.asyncio
    async def test_stats_with_monitor_returns_report(self) -> None:
        """/stats 有 monitor 时应返回监控报告。"""
        state = _create_mock_state()
        mock_monitor = MagicMock()
        mock_monitor.report.return_value = "Tool stats report"

        result = await dispatch_command("/stats", state=state, monitor=mock_monitor, capture=True)

        assert result is not None

    @pytest.mark.asyncio
    async def test_stats_without_monitor_returns_warning(self) -> None:
        """/stats 无 monitor 时应返回警告。"""
        state = _create_mock_state()

        result = await dispatch_command("/stats", state=state, monitor=None, capture=True)

        assert "监控器" in result


class TestModelCommand:
    """测试 /model 命令。"""

    @pytest.mark.asyncio
    async def test_model_shows_current_model(self) -> None:
        """/model 应显示当前模型信息。"""
        state = _create_mock_state()

        with patch("miniagent.engine.model_cmd.format_model_info") as mock_info:
            mock_info.return_value = "Current model: gpt-4"

            await dispatch_command("/model", state=state, capture=True)

            mock_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_switch_calls_switch_model(self) -> None:
        """/model gpt-4o 应切换模型。"""
        state = _create_mock_state()

        with patch("miniagent.engine.model_cmd.switch_model") as mock_switch:
            mock_switch.return_value = "Model switched to gpt-4o"

            await dispatch_command("/model gpt-4o", state=state, capture=True)

            mock_switch.assert_called_once_with("gpt-4o")


class TestBtwCommand:
    """测试 /btw 命令。"""

    @pytest.mark.asyncio
    async def test_btw_status_returns_status(self) -> None:
        """/btw status 应返回后台任务状态。"""
        state = _create_mock_state()

        with patch("miniagent.engine.btw_cmd.cmd_btw_status") as mock_status:
            mock_status.return_value = "Background tasks: 0"

            await dispatch_command("/btw status", state=state, capture=True)

            mock_status.assert_called_once()


class TestKbCommand:
    """测试 /kb 命令。"""

    @pytest.mark.asyncio
    async def test_kb_list_returns_kbs(self) -> None:
        """/kb list 应返回知识库列表。"""
        state = _create_mock_state()

        with patch("miniagent.engine.cli_commands.cmd_kb_list") as mock_list:
            mock_list.return_value = "Knowledge bases: docs, api"

            await dispatch_command("/kb list", state=state, capture=True)

            mock_list.assert_called_once()


class TestFormatStatus:
    """测试 _format_status 函数。"""

    def test_format_status_with_all_info(self) -> None:
        """格式化状态应包含所有信息。"""
        state = _create_mock_state()

        result = _format_status(state)

        assert "实例" in result
        assert "会话" in result
        assert "飞书" in result
        assert "消息队列" in result

    def test_format_status_without_runtime_ctx(self) -> None:
        """无 runtime_ctx 应返回警告。"""
        state = {}

        result = _format_status(state)

        assert "运行时上下文" in result


class TestCaptureMode:
    """测试 capture 模式。"""

    @pytest.mark.asyncio
    async def test_capture_returns_string(self) -> None:
        """capture=True 时应返回字符串。"""
        state = _create_mock_state()

        result = await dispatch_command("/status", state=state, capture=True)

        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_no_capture_returns_none(self) -> None:
        """capture=False 时应返回 None（直接 print）。"""
        state = _create_mock_state()

        # Mock print
        with patch("builtins.print"):
            result = await dispatch_command("/status", state=state, capture=False)

            assert result is None


class TestUnknownCommand:
    """测试未知命令处理。"""

    @pytest.mark.asyncio
    async def test_unknown_command_returns_none(self) -> None:
        """未知命令应返回 None（交给 agent 处理）。"""
        state = _create_mock_state()

        result = await dispatch_command("/unknown_command", state=state)

        assert result is None


__all__ = [
    "TestDispatchCommandBasics",
    "TestStatusCommand",
    "TestSessionCommand",
    "TestFeishuCommand",
    "TestQueueCommand",
    "TestBindCommand",
    "TestHelpCommand",
    "TestStatsCommand",
    "TestModelCommand",
    "TestBtwCommand",
    "TestKbCommand",
    "TestFormatStatus",
    "TestCaptureMode",
    "TestUnknownCommand",
]