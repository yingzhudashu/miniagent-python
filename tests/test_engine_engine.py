"""Tests for UnifiedEngine core functionality.

Tests cover:
- Engine initialization
- Session binding and management
- Toolbox assembly
- Thinking callback integration
- Tool finish callback
- Error handling (missing session_manager)
- Message injection
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.engine import UnifiedEngine

# ============================================================================
# Helper Function
# ============================================================================


def _mock_engine_thinking(engine: UnifiedEngine) -> None:
    """Mock ThinkingDisplay 避免控制台输出。"""
    engine.thinking.show = AsyncMock()
    engine.thinking.end_thinking = MagicMock()
    engine.thinking.enable_feishu = MagicMock()
    engine.thinking.disable_buffer = MagicMock()
    engine.thinking.reset_counter = MagicMock()
    engine.thinking.thinking_state = MagicMock(return_value=MagicMock())


def _create_mock_session_manager() -> tuple[MagicMock, MagicMock]:
    """创建 mock session_manager 和 ctx。"""
    mock_session_manager = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.conversation_history = []
    mock_ctx.files_path = "/tmp/test"
    mock_session_manager.get_or_create.return_value = mock_ctx
    return mock_session_manager, mock_ctx


# ============================================================================
# Test Classes
# ============================================================================


class TestUnifiedEngineInit:
    """测试 UnifiedEngine 初始化。"""

    def test_engine_initialization(self) -> None:
        """引擎初始化应创建 ThinkingDisplay 和内部锁。"""
        engine = UnifiedEngine()

        assert engine.thinking is not None
        assert hasattr(engine.thinking, "show")
        assert hasattr(engine.thinking, "reset_counter")
        assert engine._clarifier is None
        assert engine._exec_lock is not None

    def test_engine_has_run_method(self) -> None:
        """引擎应有 run_agent_with_thinking 方法。"""
        engine = UnifiedEngine()
        assert hasattr(engine, "run_agent_with_thinking")
        assert callable(engine.run_agent_with_thinking)

    def test_engine_has_inject_message_method(self) -> None:
        """引擎应有 inject_message 方法。"""
        engine = UnifiedEngine()
        assert hasattr(engine, "inject_message")
        assert callable(engine.inject_message)


class TestUnifiedEngineSessionBinding:
    """测试会话绑定逻辑。"""

    @pytest.mark.asyncio
    async def test_missing_session_manager_raises_error(self) -> None:
        """缺少 session_manager 时应抛出 ValueError。"""
        engine = UnifiedEngine()

        with pytest.raises(ValueError) as exc_info:
            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
            )

        assert "session_manager" in str(exc_info.value)


class TestUnifiedEngineToolboxAssembly:
    """测试工具箱组装。"""

    @pytest.mark.asyncio
    async def test_empty_toolboxes_accepted(self) -> None:
        """空工具箱列表应被接受。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Test reply"

            result = await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                session_manager=mock_session_manager,
            )

            assert result == "Test reply"

    @pytest.mark.asyncio
    async def test_toolboxes_passed_to_run_agent(self) -> None:
        """工具箱应传递给 run_agent。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Test reply"
            toolboxes = ["filesystem", "exec"]

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=toolboxes,
                skill_prompts=None,
                session_manager=mock_session_manager,
            )

            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["toolboxes"] == toolboxes


class TestUnifiedEngineThinkingCallback:
    """测试思考回调集成。"""

    @pytest.mark.asyncio
    async def test_thinking_counter_reset_per_session(self) -> None:
        """每个会话的思考计数器应重置。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Test reply"
            session_key = "test_session_reset"

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key=session_key,
                skill_toolboxes=[],
                skill_prompts=None,
                session_manager=mock_session_manager,
            )

            engine.thinking.reset_counter.assert_called_once_with(session_key)


class TestUnifiedEngineToolFinish:
    """测试工具完成回调。"""

    @pytest.mark.asyncio
    async def test_tool_finish_collects_data(self) -> None:
        """工具完成回调应收集调用数据。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()

        async def mock_run_with_tool(*args, **kwargs):
            on_tool_finish = kwargs.get("on_tool_finish")
            if on_tool_finish:
                await on_tool_finish("test_tool", '{"arg": "value"}', "output", True)
            return "Reply"

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = mock_run_with_tool

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                session_manager=mock_session_manager,
            )

            history = mock_ctx.conversation_history
            assert len(history) >= 1
            assert history[0]["role"] == "user"


class TestUnifiedEngineHistoryUpdate:
    """测试历史更新逻辑。"""

    @pytest.mark.asyncio
    async def test_history_updated_with_user_input(self) -> None:
        """历史应包含用户输入。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Reply"
            user_input = "Hello!"

            await engine.run_agent_with_thinking(
                user_input=user_input,
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                session_manager=mock_session_manager,
            )

            user_messages = [h for h in mock_ctx.conversation_history if h["role"] == "user"]
            assert len(user_messages) == 1
            assert user_messages[0]["content"] == user_input

    @pytest.mark.asyncio
    async def test_history_updated_with_assistant_reply(self) -> None:
        """历史应包含助手回复。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()
        reply = "Assistant reply."

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = reply

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                session_manager=mock_session_manager,
            )

            assistant_messages = [h for h in mock_ctx.conversation_history if h["role"] == "assistant"]
            assert len(assistant_messages) == 1
            assert assistant_messages[0]["content"] == reply


class TestUnifiedEngineMessageInjection:
    """测试消息注入功能。"""

    def test_inject_message_adds_to_history(self) -> None:
        """inject_message 应将消息添加到历史。"""
        engine = UnifiedEngine()
        mock_session_manager, mock_ctx = _create_mock_session_manager()

        engine.inject_message(
            session_key="test_session",
            content="Injected",
            session_manager=mock_session_manager,
        )

        assert len(mock_ctx.conversation_history) == 1
        assert mock_ctx.conversation_history[0]["role"] == "user"
        assert mock_ctx.conversation_history[0]["_injected"] is True

    def test_inject_message_without_session_manager(self) -> None:
        """无 session_manager 时应静默跳过。"""
        engine = UnifiedEngine()
        engine.inject_message(session_key="test", content="msg", session_manager=None)


class TestUnifiedEngineFeishuChannel:
    """测试飞书通道特有逻辑。"""

    @pytest.mark.asyncio
    async def test_feishu_requires_channel_router(self) -> None:
        """飞书通道需要 channel_router。"""
        engine = UnifiedEngine()
        mock_session_manager, _ = _create_mock_session_manager()

        with pytest.raises(ValueError) as exc_info:
            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="feishu:test",
                skill_toolboxes=[],
                skill_prompts=None,
                is_feishu=True,
                session_manager=mock_session_manager,
                feishu_config=MagicMock(),
                channel_router=None,
            )

        assert "channel_router" in str(exc_info.value)


class TestUnifiedEngineExecLock:
    """测试执行锁机制。"""

    @pytest.mark.asyncio
    async def test_exec_lock_prevents_concurrent_calls(self) -> None:
        """执行锁应防止并发调用。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        call_order = []

        async def slow_run(*args, **kwargs):
            call_order.append("start")
            import asyncio
            await asyncio.sleep(0.05)
            call_order.append("end")
            return "Reply"

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = slow_run

            import asyncio
            t1 = asyncio.create_task(engine.run_agent_with_thinking(
                "test1", "s1", [], None, session_manager=mock_session_manager
            ))
            t2 = asyncio.create_task(engine.run_agent_with_thinking(
                "test2", "s2", [], None, session_manager=mock_session_manager
            ))
            await asyncio.gather(t1, t2)

            # 串行执行: start -> end -> start -> end
            assert call_order == ["start", "end", "start", "end"]


class TestUnifiedEngineClarifier:
    """测试澄清器懒加载。"""

    def test_clarifier_none_initially(self) -> None:
        """初始时 clarifier 应为 None。"""
        engine = UnifiedEngine()
        assert engine._clarifier is None

    def test_clarifier_lazy_loaded(self) -> None:
        """启用时 clarifier 应懒加载。"""
        engine = UnifiedEngine()

        with patch("miniagent.engine.engine.get_config", return_value=True):
            with patch("miniagent.core.requirement_clarifier.RequirementClarifier") as mock_c:
                mock_c.return_value = MagicMock()
                c1 = engine._get_clarifier()
                assert c1 is not None
                c2 = engine._get_clarifier()
                assert c1 is c2


class TestUnifiedEngineConfirmationChannel:
    """测试确认通道。"""

    def test_confirmation_channel_lazy_created(self) -> None:
        """确认通道应懒加载创建。"""
        engine = UnifiedEngine()
        assert engine._confirmation_channel is None
        channel = engine._get_confirmation_channel()
        assert channel is not None

    def test_confirmation_channel_singleton(self) -> None:
        """确认通道应为单例。"""
        engine = UnifiedEngine()
        c1 = engine._get_confirmation_channel()
        c2 = engine._get_confirmation_channel()
        assert c1 is c2


class TestUnifiedEngineIntegration:
    """集成测试。"""

    @pytest.mark.asyncio
    async def test_full_cli_flow(self) -> None:
        """CLI 模式完整流程。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()
        mock_session_manager.save_session_history = MagicMock()

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Final reply"

            result = await engine.run_agent_with_thinking(
                user_input="Hello",
                session_key="cli",
                skill_toolboxes=["filesystem"],
                skill_prompts="System",
                is_feishu=False,
                session_manager=mock_session_manager,
            )

            assert result == "Final reply"
            mock_session_manager.save_session_history.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_feishu_flow(self) -> None:
        """飞书模式完整流程。"""
        engine = UnifiedEngine()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()
        mock_session_manager.save_session_history = MagicMock()

        mock_router = MagicMock()
        mock_router.get_bound_channels.return_value = []

        with patch("miniagent.engine.engine.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Feishu reply"

            # Mock finalize_feishu_thinking_stream from poll_server
            with patch("miniagent.feishu.poll_server.finalize_feishu_thinking_stream", new_callable=AsyncMock):
                result = await engine.run_agent_with_thinking(
                    user_input="Hello",
                    session_key="feishu:oc_test",
                    skill_toolboxes=["feishu_doc"],
                    skill_prompts=None,
                    is_feishu=True,
                    session_manager=mock_session_manager,
                    feishu_config=MagicMock(),
                    channel_router=mock_router,
                )

                assert result == "Feishu reply"


__all__ = [
    "TestUnifiedEngineInit",
    "TestUnifiedEngineSessionBinding",
    "TestUnifiedEngineToolboxAssembly",
    "TestUnifiedEngineThinkingCallback",
    "TestUnifiedEngineToolFinish",
    "TestUnifiedEngineHistoryUpdate",
    "TestUnifiedEngineMessageInjection",
    "TestUnifiedEngineFeishuChannel",
    "TestUnifiedEngineExecLock",
    "TestUnifiedEngineClarifier",
    "TestUnifiedEngineConfirmationChannel",
    "TestUnifiedEngineIntegration",
]