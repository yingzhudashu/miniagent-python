"""Tests for command_dispatch module.

Tests cover main commands:
- /status
- /session (list, switch, create, rename, delete)
- /feishu (start, stop, status)
- /queue (status, set, abort)
- /help
- /stats
- /model
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.command_dispatch import (
    _REGISTERED_COMMANDS,
    _find_command_by_prefix,
    _format_status,
    _get_last_qa,
    _normalize_command_text,
    dispatch_command,
)

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

        with patch(
            "miniagent.engine.cli_commands.feishu_dot_commands_full_enabled",
            return_value=False,
        ):
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
        """/feishu start 应调用 feishu.start（factory + state）。"""
        state = _create_mock_state()
        factory = state["runtime_ctx"].create_feishu_handler_factory

        result = await dispatch_command("/feishu start", state=state, capture=True)

        state["runtime_ctx"].feishu.start.assert_called_once_with(
            factory,
            state,
            user_status=None,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_feishu_stop_calls_stop_async(self) -> None:
        """/feishu stop 应 await feishu.stop_async。"""
        state = _create_mock_state()
        state["runtime_ctx"].feishu.stop_async = AsyncMock(return_value=None)

        await dispatch_command("/feishu stop", state=state, capture=True)

        state["runtime_ctx"].feishu.stop_async.assert_awaited_once()


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


class TestRemovedCommands:
    """已移除的命令不应再经 dispatch 处理。"""

    def test_bind_unbind_copy_not_registered(self) -> None:
        assert "/bind" not in _REGISTERED_COMMANDS
        assert "/unbind" not in _REGISTERED_COMMANDS
        assert "/copy" not in _REGISTERED_COMMANDS

    @pytest.mark.asyncio
    async def test_bind_not_handled_by_dispatch(self) -> None:
        state = _create_mock_state()
        result = await dispatch_command("/bind status", state=state, capture=True)
        assert result is None


class TestStatusFocusLine:
    @pytest.mark.asyncio
    async def test_status_includes_cli_focus_mode(self) -> None:
        from miniagent.infrastructure.channel_router import ChannelRouter

        state = _create_mock_state()
        router = ChannelRouter()
        router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_focus")
        router.set_primary("feishu:oc_focus")
        state["runtime_ctx"].channel_router = router

        result = await dispatch_command("/status", state=state, capture=True)
        assert result is not None
        assert "飞书群聊" in result or "聚焦" in result


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


class TestSelfOptCommand:
    """测试 /self-opt capture 路径（全屏 CLI / 飞书）。"""

    @pytest.mark.asyncio
    async def test_self_opt_status_capture_returns_text(self) -> None:
        """capture=True 时 /self-opt status 应返回非 None 字符串。"""
        state = _create_mock_state()

        with patch(
            "miniagent.engine.cli_commands.cmd_self_opt_status",
            side_effect=lambda: print("自我优化状态 OK"),
        ):
            result = await dispatch_command("/self-opt status", state=state, capture=True)

        assert result is not None
        assert isinstance(result, str)
        assert "自我优化状态 OK" in result

    @pytest.mark.asyncio
    async def test_self_opt_unknown_subcommand_capture(self) -> None:
        """未知子命令应返回用法提示，而非 None。"""
        state = _create_mock_state()

        result = await dispatch_command("/self-opt bogus", state=state, capture=True)

        assert result is not None
        assert "未知的子命令" in result
        assert "status|proposals" in result

    @pytest.mark.asyncio
    async def test_self_opt_disabled_capture(self) -> None:
        """功能关闭时 capture 应返回关闭提示。"""
        state = _create_mock_state()

        with patch(
            "miniagent.infrastructure.json_config.get_config",
            return_value=False,
        ):
            result = await dispatch_command("/self-opt status", state=state, capture=True)

        assert result is not None
        assert "自我优化功能已关闭" in result

    @pytest.mark.asyncio
    async def test_self_opt_show_missing_id_capture(self) -> None:
        """缺参时应返回用法提示，而非「未知子命令」。"""
        state = _create_mock_state()

        result = await dispatch_command("/self-opt show", state=state, capture=True)

        assert result is not None
        assert "用法: /self-opt show <id>" in result
        assert "未知的子命令" not in result

    @pytest.mark.asyncio
    async def test_self_opt_apply_capture_returns_text(self) -> None:
        """apply 异步子命令在 capture=True 时应返回捕获文本。"""
        state = _create_mock_state()

        async def _fake_apply(proposal_id: str, root: str = "") -> None:
            print(f"已执行提案 {proposal_id} root={root}")

        with patch(
            "miniagent.engine.cli_commands.cmd_self_opt_apply",
            side_effect=_fake_apply,
        ):
            result = await dispatch_command(
                "/self-opt apply pid-1 /tmp/root",
                state=state,
                capture=True,
            )

        assert result is not None
        assert "已执行提案 pid-1" in result


class TestLegacyReloadSkills:
    """旧版 ``.reload-skills`` 别名。"""

    def test_normalize_legacy_dot_prefix(self) -> None:
        assert _normalize_command_text(".reload-skills") == "/reload-skills"
        assert _normalize_command_text(".reload_skills") == "/reload-skills"
        assert _normalize_command_text("hello") is None

    @pytest.mark.asyncio
    async def test_legacy_reload_skills_dispatches(self) -> None:
        state = _create_mock_state()

        with patch("miniagent.skills.refresh.refresh_skills", new_callable=AsyncMock) as mock_refresh:
            from miniagent.skills.refresh import RefreshResult

            mock_refresh.return_value = RefreshResult(
                package_ids=["pkg"],
                loaded_skills=[],
                added_tools=[],
                removed_tools=[],
            )

            result = await dispatch_command(".reload-skills", state=state, capture=True)

        assert result is not None
        assert "技能已重新加载" in result
        mock_refresh.assert_awaited_once()


class TestReviewCommand:
    """测试 /review capture 路径。"""

    @pytest.mark.asyncio
    async def test_review_capture_empty_when_handled_via_term_write(self) -> None:
        """capture=True 且 _run_review 返回 None 时应返回空串，避免 fallthrough。"""
        state = _create_mock_state()
        session = MagicMock()
        session.conversation_history = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ]
        state["session_manager"].get.return_value = session

        with patch(
            "miniagent.engine.command_dispatch._run_review",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await dispatch_command("/review", state=state, capture=True)

        assert result == ""

    def test_get_last_qa_pairs_consecutive_messages(self) -> None:
        """未回复的 user 消息不应与更早的 assistant 错配。"""
        sm = MagicMock()
        session = MagicMock()
        session.conversation_history = [
            {"role": "user", "content": "old Q"},
            {"role": "assistant", "content": "old A"},
            {"role": "user", "content": "new Q without reply"},
        ]
        sm.get.return_value = session

        user, assistant = _get_last_qa(sm, "sid")

        assert user == "old Q"
        assert assistant == "old A"


class TestPrefixMatchAmbiguity:
    """前缀匹配歧义（文档化行为）。"""

    def test_sta_matches_stats_first_in_registry(self) -> None:
        assert _find_command_by_prefix("/sta") == "/stats"


__all__ = [
    "TestDispatchCommandBasics",
    "TestStatusCommand",
    "TestSessionCommand",
    "TestFeishuCommand",
    "TestQueueCommand",
    "TestHelpCommand",
    "TestStatsCommand",
    "TestModelCommand",
    "TestBtwCommand",
    "TestKbCommand",
    "TestFormatStatus",
    "TestCaptureMode",
    "TestUnknownCommand",
    "TestSelfOptCommand",
    "TestLegacyReloadSkills",
    "TestReviewCommand",
    "TestPrefixMatchAmbiguity",
]