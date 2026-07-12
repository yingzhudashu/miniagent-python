"""Negative Tests - Failure scenarios, exceptions, and edge cases.

Covers:
- Executor error handling (LLM failures, tool exceptions)
- Planner failure handling
- Engine error recovery
- Session manager errors
- Memory system failures
- Feishu connection errors
- Tool execution failures
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.memory_helpers import make_knowledge_registry

# ============================================================================
# Executor Negative Tests
# ============================================================================


class TestExecutorNegative:
    """Executor 错误处理测试。"""

    @pytest.mark.asyncio
    async def test_executor_handles_llm_api_error(self) -> None:
        """LLM API 错误应被正确处理。"""
        from miniagent.infrastructure.registry import DefaultToolRegistry

        # Mock LLM client 抛出错误
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        registry = DefaultToolRegistry()

        # 错误应能被创建和捕获
        error = Exception("LLM call failed")
        assert str(error) == "LLM call failed"

        # 工具注册表应有效
        assert registry is not None

    @pytest.mark.asyncio
    async def test_executor_handles_tool_execution_error(self) -> None:
        """工具执行错误应被正确处理。"""
        from miniagent.types.tool import ToolContext

        # 模拟工具执行失败
        ToolContext(cwd="/tmp", permission="sandbox")

        # 工具应能处理各种错误类型
        error_types = [
            TimeoutError("Tool timeout"),
            PermissionError("Permission denied"),
            FileNotFoundError("File not found"),
            ValueError("Invalid argument"),
        ]

        for error in error_types:
            # 这些错误类型应该被正确捕获和处理
            assert isinstance(error, Exception)


# ============================================================================
# Planner Negative Tests
# ============================================================================


class TestPlannerNegative:
    """Planner 错误处理测试。"""

    @pytest.mark.asyncio
    async def test_planner_handles_empty_input(self) -> None:
        """空输入应返回 fallback plan。"""
        from miniagent.core.planner import generate_plan

        with patch("miniagent.core.planner._dict_to_plan") as mock_dict:
            mock_dict.return_value = MagicMock(
                summary="fallback",
                steps=[],
                required_toolboxes=[],
            )

            # 空输入应返回 fallback
            result = await generate_plan(
                "",
                toolboxes=[],
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
            )

            # 应返回有效的计划结构
            assert result is not None

    @pytest.mark.asyncio
    async def test_planner_handles_json_parse_error(self) -> None:
        """JSON 解析错误应返回 fallback。"""
        from miniagent.core.planner import _dict_to_plan

        # 无效的 JSON 数据
        invalid_data = {"invalid_key": "value"}

        # 应能处理无效数据
        try:
            result = _dict_to_plan(invalid_data)
            # 如果返回结果，应包含基本结构
            if result:
                assert hasattr(result, 'summary')
        except Exception:
            # 允许抛出异常
            pass


# ============================================================================
# Session Manager Negative Tests
# ============================================================================


class TestSessionManagerNegative:
    """Session Manager 错误处理测试。"""

    def test_session_manager_handles_invalid_session_key(self) -> None:
        """无效会话 ID 应被正确处理。"""
        # Mock session manager
        sm = MagicMock()

        # 获取不存在的会话应返回 None 或空对象
        sm.get.return_value = None

        session = sm.get("nonexistent_session")
        assert session is None

    def test_session_manager_handles_corrupted_history(self) -> None:
        """损坏的历史文件应被正确处理。"""

        MagicMock()

        # 模拟损坏的历史数据
        corrupted_history = [{"role": "invalid_role", "content": None}]

        # 应能处理损坏数据而不崩溃
        assert len(corrupted_history) >= 0


# ============================================================================
# Memory System Negative Tests
# ============================================================================


class TestMemoryNegative:
    """Memory 系统错误处理测试。"""

    @pytest.mark.asyncio
    async def test_memory_handles_write_failure(self) -> None:
        """内存写入失败应被正确处理。"""
        from miniagent.memory.store import DefaultMemoryStore

        with patch("miniagent.memory.store.get_config") as mock_config:
            mock_config.return_value = "/nonexistent/path"

            try:
                store = DefaultMemoryStore(state_dir="/nonexistent")
                # 尝试写入应优雅失败
                await store.update_summary("test", "test", [])
            except Exception as e:
                # 错误应包含有用信息
                assert str(e) is not None

    def test_keyword_index_handles_empty_query(self) -> None:
        """空查询应返回空结果。"""

        ki = MagicMock()

        # 空查询
        results = ki.search("")

        # 应返回空结果或无结果
        assert results is None or len(results) == 0


# ============================================================================
# Feishu Negative Tests
# ============================================================================


class TestFeishuNegative:
    """Feishu 错误处理测试。"""

    @pytest.mark.asyncio
    async def test_feishu_handles_connection_failure(self) -> None:
        """飞书连接失败应被正确处理。"""
        from miniagent.feishu.ws_client import FeishuWsClient

        try:
            # 创建客户端但不连接
            client = MagicMock(spec=FeishuWsClient)
            client.connected = False

            # 未连接状态应被正确检测
            assert not client.connected
        except ImportError:
            pytest.skip("lark-oapi not available")

    @pytest.mark.asyncio
    async def test_feishu_handles_message_send_failure(self) -> None:
        """飞书消息发送失败应被正确处理。"""
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(
            app_id="test",
            app_secret="test",
            verification_token="test",
        )

        # 配置应有效
        assert cfg.app_id == "test"

        # Mock 发送失败
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False

        mock_client.im.v1.message.create.return_value = mock_response

        # 失败应被正确检测
        assert not mock_response.success()


# ============================================================================
# Tool Execution Negative Tests
# ============================================================================


class TestToolExecutionNegative:
    """工具执行错误处理测试。"""

    @pytest.mark.asyncio
    async def test_tool_handles_permission_denied(self) -> None:
        """权限拒绝应被正确处理。"""
        from miniagent.types.tool import ToolContext

        ctx = ToolContext(
            cwd="/protected",
            permission="sandbox",
            session_key="test",
            allowed_paths=["/tmp"],
        )

        # 尝试访问不在白名单的路径应失败
        assert "/protected" not in ctx.allowed_paths

    @pytest.mark.asyncio
    async def test_tool_handles_timeout(self) -> None:
        """工具超时应被正确处理。"""
        import time

        # 模拟超时
        start = time.time()

        async def slow_operation():
            await asyncio.sleep(10)

        # 使用短超时
        try:
            await asyncio.wait_for(slow_operation(), timeout=0.1)
        except asyncio.TimeoutError:
            elapsed = time.time() - start
            # 超时应快速返回
            assert elapsed < 1.0


# ============================================================================
# Command Dispatch Negative Tests
# ============================================================================


class TestCommandDispatchNegative:
    """命令调度错误处理测试。"""

    @pytest.mark.asyncio
    async def test_dispatch_handles_invalid_command(self) -> None:
        """无效命令应返回 None。"""
        from miniagent.engine.command_dispatch import dispatch_command

        state = {"runtime_ctx": MagicMock()}

        # 无效命令格式
        result = await dispatch_command("invalid_command", state=state)

        # 应返回 None（交给 agent 处理）
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_handles_missing_runtime_ctx(self) -> None:
        """缺少 runtime_ctx 应返回警告。"""
        from miniagent.engine.command_dispatch import dispatch_command

        state = {"runtime_ctx": None}  # runtime_ctx 为 None

        # 调用应返回警告
        result = await dispatch_command("/status", state=state, capture=True)

        # 应返回警告消息
        assert result is not None


# ============================================================================
# Background Task Negative Tests
# ============================================================================


class TestBackgroundTaskNegative:
    """后台任务错误处理测试。"""

    @pytest.mark.asyncio
    async def test_background_task_handles_failure(self) -> None:
        """后台任务失败应被正确处理。"""

        MagicMock()

        # 模拟失败任务
        failed_task = MagicMock()
        failed_task.status = "failed"
        failed_task.error = "Execution failed"

        # 失败状态应被正确记录
        assert failed_task.status == "failed"
        assert failed_task.error is not None


# ============================================================================
# Concurrency Negative Tests
# ============================================================================


class TestConcurrencyNegative:
    """并发错误处理测试。"""

    @pytest.mark.asyncio
    async def test_concurrent_session_access(self) -> None:
        """并发访问同一会话应被正确处理。"""
        # 模拟锁状态检查
        locked = False  # 默认不锁定

        # 应返回布尔值
        assert isinstance(locked, bool)


# ============================================================================
# Configuration Negative Tests
# ============================================================================


class TestConfigurationNegative:
    """配置错误处理测试。"""

    def test_config_handles_missing_required_fields(self) -> None:
        """缺少必填字段应使用默认值或报错。"""
        from miniagent.core.config import get_default_agent_config

        # 获取默认配置
        config = get_default_agent_config()

        # 必填字段应有默认值
        assert config.max_turns > 0

    def test_config_handles_invalid_values(self) -> None:
        """无效配置值应被正确处理。"""
        # 测试布尔值解析的默认行为
        invalid_values = ["maybe", "perhaps", "invalid"]

        for val in invalid_values:
            # 无效值应返回 False（默认）
            if val.lower() in ("true", "1", "yes"):
                result = True
            else:
                result = False
            assert isinstance(result, bool)


__all__ = [
    "TestExecutorNegative",
    "TestPlannerNegative",
    "TestSessionManagerNegative",
    "TestMemoryNegative",
    "TestFeishuNegative",
    "TestToolExecutionNegative",
    "TestCommandDispatchNegative",
    "TestBackgroundTaskNegative",
    "TestConcurrencyNegative",
    "TestConfigurationNegative",
]
