"""Tests for AssistantTurnService core functionality.

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

from miniagent.agent.types.agent import AgentRunResult
from miniagent.assistant.engine.turn_service import AssistantTurnService
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime

# ============================================================================
# Helper Function
# ============================================================================


def _mock_engine_thinking(engine: AssistantTurnService) -> None:
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
    mock_session_manager.get_session_files_path.return_value = mock_ctx.files_path
    mock_session_manager.save_session_history_async = AsyncMock()
    return mock_session_manager, mock_ctx


# ============================================================================
# Test Classes
# ============================================================================


class TestAssistantTurnServiceInit:
    """测试 AssistantTurnService 初始化。"""

    def test_engine_initialization(self) -> None:
        """引擎初始化应创建 ThinkingDisplay 和内部锁。"""
        engine = AssistantTurnService()

        assert engine.thinking is not None
        assert hasattr(engine.thinking, "show")
        assert hasattr(engine.thinking, "reset_counter")
        assert engine._clarifier is None
        assert engine._session_exec is not None
        assert engine._session_exec.parallel_sessions is True

    def test_engine_has_run_method(self) -> None:
        """引擎应有 run_agent_with_thinking 方法。"""
        engine = AssistantTurnService()
        assert hasattr(engine, "run_agent_with_thinking")
        assert callable(engine.run_agent_with_thinking)

    def test_engine_has_inject_message_method(self) -> None:
        """引擎应有 inject_message 方法。"""
        engine = AssistantTurnService()
        assert hasattr(engine, "inject_message")
        assert callable(engine.inject_message)


class TestAssistantTurnServiceSessionBinding:
    """测试会话绑定逻辑。"""

    @pytest.mark.asyncio
    async def test_missing_session_manager_raises_error(self) -> None:
        """缺少 session_manager 时应抛出 ValueError。"""
        engine = AssistantTurnService()

        with pytest.raises(ValueError) as exc_info:
            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
            )

        assert "session_manager" in str(exc_info.value)


class TestAssistantTurnServiceToolboxAssembly:
    """测试工具箱组装。"""

    @pytest.mark.asyncio
    async def test_empty_toolboxes_accepted(self) -> None:
        """空工具箱列表应被接受。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply="Test reply")

            result = await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                session_manager=mock_session_manager,
            )

            assert result == "Test reply"

    @pytest.mark.asyncio
    async def test_toolboxes_passed_to_run_agent(self) -> None:
        """工具箱应传递给 run_agent。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply="Test reply")
            toolboxes = ["filesystem", "exec"]

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=toolboxes,
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                session_manager=mock_session_manager,
            )

            turn = mock_run.call_args.args[0]
            assert turn.toolboxes == tuple(toolboxes)


class TestAssistantTurnServiceThinkingCallback:
    """测试思考回调集成。"""

    @pytest.mark.asyncio
    async def test_thinking_counter_reset_per_session(self) -> None:
        """每个会话的思考计数器应重置。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply="Test reply")
            session_key = "test_session_reset"

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key=session_key,
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                session_manager=mock_session_manager,
            )

            engine.thinking.reset_counter.assert_called_once_with(session_key)


class TestAssistantTurnServiceToolFinish:
    """测试工具完成回调。"""

    @pytest.mark.asyncio
    async def test_tool_finish_collects_data(self) -> None:
        """工具完成回调应收集调用数据。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()

        async def mock_run_with_tool(turn):
            on_tool_finish = turn.on_tool_finish
            if on_tool_finish:
                await on_tool_finish("test_tool", '{"arg": "value"}', "output", True)
            return AgentRunResult(reply="Reply")

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = mock_run_with_tool

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                session_manager=mock_session_manager,
            )

            history = mock_ctx.conversation_history
            assert len(history) >= 1
            assert history[0]["role"] == "user"


class TestAssistantTurnServiceHistoryUpdate:
    """测试历史更新逻辑。"""

    @pytest.mark.asyncio
    async def test_history_updated_with_user_input(self) -> None:
        """历史应包含用户输入。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply="Reply")
            user_input = "Hello!"

            await engine.run_agent_with_thinking(
                user_input=user_input,
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                session_manager=mock_session_manager,
            )

            user_messages = [h for h in mock_ctx.conversation_history if h["role"] == "user"]
            assert len(user_messages) == 1
            assert user_messages[0]["content"] == user_input

    @pytest.mark.asyncio
    async def test_history_updated_with_assistant_reply(self) -> None:
        """历史应包含助手回复。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()
        reply = "Assistant reply."

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply=reply)

            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="test_session",
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                session_manager=mock_session_manager,
            )

            assistant_messages = [h for h in mock_ctx.conversation_history if h["role"] == "assistant"]
            assert len(assistant_messages) == 1
            assert assistant_messages[0]["content"] == reply


class TestAssistantTurnServiceMessageInjection:
    """测试消息注入功能。"""

    def test_inject_message_adds_to_history(self) -> None:
        """inject_message 应将消息添加到历史。"""
        engine = AssistantTurnService()
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
        engine = AssistantTurnService()
        engine.inject_message(session_key="test", content="msg", session_manager=None)


class TestAssistantTurnServiceFeishuChannel:
    """测试飞书通道特有逻辑。"""

    @pytest.mark.asyncio
    async def test_feishu_requires_channel_router(self) -> None:
        """飞书通道需要 channel_router。"""
        engine = AssistantTurnService()
        mock_session_manager, _ = _create_mock_session_manager()

        with pytest.raises(ValueError) as exc_info:
            await engine.run_agent_with_thinking(
                user_input="test",
                session_key="feishu:test",
                skill_toolboxes=[],
                skill_prompts=None,
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                is_feishu=True,
                session_manager=mock_session_manager,
                feishu_config=MagicMock(),
                channel_router=None,
            )

        assert "channel_router" in str(exc_info.value)


class TestAssistantTurnServiceExecLock:
    """测试执行锁机制。"""

    @pytest.mark.asyncio
    async def test_same_session_serial(self) -> None:
        """同 session_key 应串行执行。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        call_order = []

        async def slow_run(*args, **kwargs):
            call_order.append("start")
            import asyncio
            await asyncio.sleep(0.05)
            call_order.append("end")
            return AgentRunResult(reply="Reply")

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = slow_run

            import asyncio
            t1 = asyncio.create_task(engine.run_agent_with_thinking(
                "test1", "s1", [], None, memory=make_memory_runtime(), knowledge_registry=make_knowledge_registry(), client=MagicMock(), session_manager=mock_session_manager
            ))
            t2 = asyncio.create_task(engine.run_agent_with_thinking(
                "test2", "s1", [], None, memory=make_memory_runtime(), knowledge_registry=make_knowledge_registry(), client=MagicMock(), session_manager=mock_session_manager
            ))
            await asyncio.gather(t1, t2)

            assert call_order == ["start", "end", "start", "end"]

    @pytest.mark.asyncio
    async def test_different_sessions_can_overlap(self) -> None:
        """不同 session_key 在 parallel_sessions 开启时可并行。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, _ = _create_mock_session_manager()

        in_flight = 0
        overlap = False

        async def slow_run(*args, **kwargs):
            nonlocal in_flight, overlap
            in_flight += 1
            if in_flight >= 2:
                overlap = True
            import asyncio
            await asyncio.sleep(0.08)
            in_flight -= 1
            return AgentRunResult(reply="Reply")

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = slow_run

            import asyncio
            t1 = asyncio.create_task(engine.run_agent_with_thinking(
                "test1", "s1", [], None, memory=make_memory_runtime(), knowledge_registry=make_knowledge_registry(), client=MagicMock(), session_manager=mock_session_manager
            ))
            t2 = asyncio.create_task(engine.run_agent_with_thinking(
                "test2", "s2", [], None, memory=make_memory_runtime(), knowledge_registry=make_knowledge_registry(), client=MagicMock(), session_manager=mock_session_manager
            ))
            await asyncio.gather(t1, t2)

            assert overlap is True


class TestAssistantTurnServiceClarifier:
    """测试澄清器懒加载。"""

    def test_clarifier_none_initially(self) -> None:
        """初始时 clarifier 应为 None。"""
        engine = AssistantTurnService()
        assert engine._clarifier is None

    def test_clarifier_lazy_loaded(self) -> None:
        """启用时 clarifier 应懒加载。"""
        engine = AssistantTurnService()

        with patch("miniagent.assistant.engine.turn_service.get_config", return_value=True):
            with patch("miniagent.agent.requirement_clarifier.RequirementClarifier") as mock_c:
                mock_c.return_value = MagicMock()
                c1 = engine._get_clarifier()
                assert c1 is not None
                c2 = engine._get_clarifier()
                assert c1 is c2


class TestAssistantTurnServiceConfirmationChannel:
    """测试确认通道。"""

    def test_confirmation_channel_lazy_created(self) -> None:
        """确认通道应懒加载创建。"""
        engine = AssistantTurnService()
        assert engine._confirmation_channels == {}
        channel = engine._get_confirmation_channel("s1")
        assert channel is not None

    def test_confirmation_channel_per_session(self) -> None:
        """不同 session_key 应有独立确认通道。"""
        engine = AssistantTurnService()
        c1 = engine._get_confirmation_channel("s1")
        c2 = engine._get_confirmation_channel("s2")
        c1_again = engine._get_confirmation_channel("s1")
        assert c1 is c1_again
        assert c1 is not c2


class TestAssistantTurnServiceReflectionCache:
    """测试反思评估缓存。"""

    def test_get_and_clear_last_reflection(self) -> None:
        engine = AssistantTurnService()
        engine._last_reflection["s1"] = {"score": 0.9}
        assert engine.get_last_reflection("s1") == {"score": 0.9}
        assert engine.get_last_reflection("missing") is None
        engine.clear_last_reflection("s1")
        assert engine.get_last_reflection("s1") is None


class TestAssistantTurnServiceActiveSessionRouting:
    """测试 CLI 活跃会话与确认通道路由。"""

    def test_set_active_session_key_routes_confirmation_channel(self) -> None:
        engine = AssistantTurnService()
        c_default = engine.get_confirmation_channel("default")
        c_other = engine.get_confirmation_channel("other")
        engine.set_active_session_key("other")
        assert engine.confirmation_channel is c_other
        assert engine.confirmation_channel is not c_default


class TestAssistantTurnServicePlanHandler:
    """测试计划确认回调。"""

    @pytest.mark.asyncio
    async def test_on_plan_handler_uses_confirmation_channel(self) -> None:
        from miniagent.agent.types.confirmation import ConfirmationResult

        engine = AssistantTurnService()
        channel = engine.get_confirmation_channel("plan_session")
        channel.request_confirmation = AsyncMock(
            return_value=ConfirmationResult(approved=False)
        )

        handler = engine._on_plan_handler("plan_session")
        plan = MagicMock(requires_confirmation=True)

        with patch("miniagent.agent.agent._format_plan_display_short", return_value="summary"):
            with patch("miniagent.agent.agent._format_plan_message", return_value="full"):
                result = await handler(plan)

        assert result.approved is False
        assert result.rejected is False
        channel.request_confirmation.assert_awaited_once()
        req = channel.request_confirmation.await_args.args[0]
        assert req.full_content == "full"
        assert req.context.get("requires_confirmation") is True


class TestAssistantTurnServiceIntegration:
    """集成测试。"""

    @pytest.mark.asyncio
    async def test_full_cli_flow(self) -> None:
        """CLI 模式完整流程。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()
        mock_session_manager.save_session_history_async = AsyncMock()
        memory_runtime = make_memory_runtime()

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply="Final reply")

            result = await engine.run_agent_with_thinking(
                user_input="Hello",
                session_key="cli",
                skill_toolboxes=["filesystem"],
                skill_prompts="System",
                memory=memory_runtime,
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                is_feishu=False,
                session_manager=mock_session_manager,
            )

            assert result == "Final reply"
            mock_session_manager.save_session_history_async.assert_awaited_once()
            memory_runtime.store.update_summary.assert_not_awaited()
            memory_runtime.activity_log.log_session_start.assert_not_called()
            memory_runtime.activity_log.log_final_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_feishu_flow(self) -> None:
        """飞书模式完整流程。"""
        engine = AssistantTurnService()
        _mock_engine_thinking(engine)
        mock_session_manager, mock_ctx = _create_mock_session_manager()
        mock_session_manager.save_session_history_async = AsyncMock()

        mock_router = MagicMock()
        mock_router.get_bound_channels.return_value = []

        with patch("miniagent.agent.agent._run_agent_turn", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AgentRunResult(reply="Feishu reply")


            # Mock finalize_feishu_thinking_stream from poll_server
            with patch("miniagent.assistant.feishu.poll_server.finalize_feishu_thinking_stream", new_callable=AsyncMock):
                result = await engine.run_agent_with_thinking(
                    user_input="Hello",
                    session_key="feishu:oc_test",
                    skill_toolboxes=["feishu_doc"],
                    skill_prompts=None,
                    memory=make_memory_runtime(),
                    knowledge_registry=make_knowledge_registry(),
                    client=MagicMock(),
                    is_feishu=True,
                    session_manager=mock_session_manager,
                    feishu_config=MagicMock(),
                    channel_router=mock_router,
                )

                assert result == "Feishu reply"


__all__ = [
    "TestAssistantTurnServiceInit",
    "TestAssistantTurnServiceSessionBinding",
    "TestAssistantTurnServiceToolboxAssembly",
    "TestAssistantTurnServiceThinkingCallback",
    "TestAssistantTurnServiceToolFinish",
    "TestAssistantTurnServiceHistoryUpdate",
    "TestAssistantTurnServiceMessageInjection",
    "TestAssistantTurnServiceFeishuChannel",
    "TestAssistantTurnServiceExecLock",
    "TestAssistantTurnServiceClarifier",
    "TestAssistantTurnServiceConfirmationChannel",
    "TestAssistantTurnServiceReflectionCache",
    "TestAssistantTurnServiceActiveSessionRouting",
    "TestAssistantTurnServicePlanHandler",
    "TestAssistantTurnServiceIntegration",
]
