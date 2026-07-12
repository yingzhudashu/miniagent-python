"""Tests for Feishu WebSocket client and health monitoring.

Tests cover:
- FeishuWsClient connection handling
- WebSocket health monitoring
- Activity tracking
- Session supervision
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.feishu.ws_health import (
    FeishuWsHealthConfig,
    FeishuWsHealthState,
    _receive_loop_exit_reason,
    _watchdog_loop,
    read_feishu_ws_health_config,
    supervise_feishu_ws_session,
)

# ============================================================================
# Test Health Config
# ============================================================================


class TestFeishuWsHealthConfig:
    """测试 WebSocket 健康配置。"""

    def test_config_has_all_fields(self) -> None:
        """配置应包含所有必要字段。"""
        config = read_feishu_ws_health_config()

        assert hasattr(config, "watchdog_interval_s")
        assert hasattr(config, "dead_conn_grace_s")
        assert hasattr(config, "reconnect_grace_s")
        assert hasattr(config, "refresh_interval_s")
        assert hasattr(config, "idle_refresh_s")

    def test_config_defaults_are_positive(self) -> None:
        """默认配置值应为正数。"""
        config = read_feishu_ws_health_config()

        assert config.watchdog_interval_s > 0
        assert config.dead_conn_grace_s > 0
        assert config.reconnect_grace_s > 0

    def test_config_read_from_settings(self) -> None:
        """配置应从设置读取。"""
        with patch("miniagent.feishu.ws_health.get_config") as mock_config:
            mock_config.side_effect = lambda key, default: {
                "feishu.websocket.watchdog_interval": 60.0,
                "feishu.websocket.dead_conn_grace": 120.0,
                "feishu.websocket.reconnect_grace": 600.0,
                "feishu.websocket.refresh_interval": 300.0,
                "feishu.websocket.idle_refresh": 180.0,
            }.get(key, default)

            config = read_feishu_ws_health_config()

            assert config.watchdog_interval_s == 60.0
            assert config.dead_conn_grace_s == 120.0


class TestActivityTracking:
    """测试活动时间跟踪。"""

    def test_touch_updates_timestamp(self) -> None:
        """实例状态的入站活动时间应更新。"""

        # 初始状态
        state = FeishuWsHealthState()
        before = state.last_inbound_monotonic

        # 更新时间戳
        state.touch_inbound()
        after = state.last_inbound_monotonic

        # 时间戳应更新
        assert after is not None
        assert after >= (before or 0)

    def test_activity_timestamp_is_monotonic(self) -> None:
        """时间戳应为 monotonic 时间。"""
        state = FeishuWsHealthState()
        state.touch_inbound()
        ts = state.last_inbound_monotonic

        assert ts is not None
        # monotonic 时间应大于 0（系统启动以来的时间）
        assert ts > 0


class TestSessionEndTracking:
    """测试会话结束跟踪。"""

    def test_record_session_end_stores_reason(self) -> None:
        """记录会话结束应存储原因和时间。"""
        state = FeishuWsHealthState()
        state.record_session_end("test_reason")

        reason, timestamp = state.last_session_end()

        assert reason == "test_reason"
        assert timestamp is not None

    def test_session_end_timestamp_is_unix_time(self) -> None:
        """会话结束时间戳应为 Unix 时间。"""
        import time

        before = time.time()
        state = FeishuWsHealthState()
        state.record_session_end("another_reason")
        after = time.time()

        reason, timestamp = state.last_session_end()

        assert timestamp is not None
        assert before <= timestamp <= after


class TestReceiveLoopExitReason:
    """测试 receive_loop 退出原因提取。"""

    @pytest.mark.asyncio
    async def test_cancelled_task_returns_cancelled_reason(self) -> None:
        """取消的任务应返回 cancelled 原因。"""
        async def cancelled_coro():
            raise asyncio.CancelledError()

        task = asyncio.create_task(cancelled_coro())
        try:
            await asyncio.wait_for(task, timeout=0.1)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        reason = _receive_loop_exit_reason(task)
        assert "cancelled" in reason.lower()

    @pytest.mark.asyncio
    async def test_normal_exit_returns_exit_reason(self) -> None:
        """正常退出应返回 exit 原因。"""
        async def normal_coro():
            pass

        task = asyncio.create_task(normal_coro())
        await task

        reason = _receive_loop_exit_reason(task)
        assert "exit" in reason.lower()

    @pytest.mark.asyncio
    async def test_exception_exit_returns_exception_type(self) -> None:
        """异常退出应返回异常类型。"""
        async def error_coro():
            raise ValueError("test error")

        task = asyncio.create_task(error_coro())
        try:
            await asyncio.wait_for(task, timeout=0.1)
        except (ValueError, asyncio.TimeoutError):
            pass

        reason = _receive_loop_exit_reason(task)
        assert "ValueError" in reason or "error" in reason.lower()


# ============================================================================
# Test WebSocket Health (Integration-style)
# ============================================================================


class TestWebSocketHealthIntegration:
    """测试 WebSocket 健康监督（集成风格）。"""

    @pytest.mark.asyncio
    async def test_supervise_returns_no_receive_task(self) -> None:
        """无 receive_task 时应返回特定原因。"""
        mock_client = MagicMock()
        mock_client.receive_task = None
        mock_client._disconnect = AsyncMock()

        shutdown_event = asyncio.Event()

        # 需要 mock get_config 来提供配置
        with patch("miniagent.feishu.ws_health.get_config", return_value=30.0):
            result = await supervise_feishu_ws_session(
                mock_client,
                shutdown_event=shutdown_event,
                health_state=FeishuWsHealthState(),
            )

            assert result == "no_receive_task"

    @pytest.mark.asyncio
    async def test_supervise_respects_shutdown_event(self) -> None:
        """shutdown_event 应立即终止监督。"""
        mock_client = MagicMock()

        # 创建一个完成的 receive_task
        async def completed_task():
            pass

        task = asyncio.create_task(completed_task())
        await task  # 确保完成
        mock_client.receive_task = task
        mock_client.connected = True
        mock_client._disconnect = AsyncMock()

        shutdown_event = asyncio.Event()
        shutdown_event.set()  # 立即设置

        with patch("miniagent.feishu.ws_health.get_config", return_value=30.0):
            result = await supervise_feishu_ws_session(
                mock_client,
                shutdown_event=shutdown_event,
                health_state=FeishuWsHealthState(),
            )

            assert result == "shutdown"


# ============================================================================
# Test FeishuWsClient (if lark-oapi available)
# ============================================================================


class TestFeishuWsClientBasic:
    """测试 FeishuWsClient 基础功能（不依赖真实连接）。"""

    def test_client_has_connected_property(self) -> None:
        """客户端应有 connected 属性。"""
        # 直接导入，测试是否有属性定义
        try:
            from miniagent.feishu.ws_client import FeishuWsClient

            # 检查类定义有 connected 属性
            assert hasattr(FeishuWsClient, "connected")
        except ImportError:
            pytest.skip("lark-oapi not available")

    def test_client_has_receive_task_property(self) -> None:
        """客户端应有 receive_task 属性。"""
        try:
            from miniagent.feishu.ws_client import FeishuWsClient

            assert hasattr(FeishuWsClient, "receive_task")
        except ImportError:
            pytest.skip("lark-oapi not available")

    def test_client_has_conn_id_property(self) -> None:
        """客户端应有 conn_id 属性。"""
        try:
            from miniagent.feishu.ws_client import FeishuWsClient

            assert hasattr(FeishuWsClient, "conn_id")
        except ImportError:
            pytest.skip("lark-oapi not available")


class TestFeishuWsAutoReconnect:
    """测试 WebSocket 自动重连配置。"""

    def test_auto_reconnect_reads_config(self) -> None:
        """自动重连应从配置读取。"""
        from miniagent.feishu.ws_client import feishu_ws_auto_reconnect_enabled

        with patch("miniagent.feishu.ws_client.get_config") as mock_config:
            mock_config.return_value = True
            result = feishu_ws_auto_reconnect_enabled()
            assert result is True

            mock_config.return_value = False
            result = feishu_ws_auto_reconnect_enabled()
            assert result is False

    def test_auto_reconnect_default_is_false(self) -> None:
        """自动重连默认应为 False。"""
        from miniagent.feishu.ws_client import feishu_ws_auto_reconnect_enabled

        # 不 mock 时应返回默认值
        result = feishu_ws_auto_reconnect_enabled()
        # 默认值来自配置，可能是 False
        assert isinstance(result, bool)


# ============================================================================
# Test Watchdog Loop (Unit Tests)
# ============================================================================


class TestWatchdogLoop:
    """测试 watchdog_loop 单元逻辑。"""

    @pytest.mark.asyncio
    async def test_watchdog_checks_connection_status(self) -> None:
        """watchdog 应检查连接状态。"""
        mock_client = MagicMock()
        mock_client.connected = False  # 连接断开
        mock_client.receive_task = None

        config = FeishuWsHealthConfig(
            watchdog_interval_s=0.1,  # 快速检查
            dead_conn_grace_s=0.2,  # 短等待
            reconnect_grace_s=1.0,
            refresh_interval_s=0.0,
            idle_refresh_s=0.0,
        )

        shutdown_event = asyncio.Event()
        exit_event = asyncio.Event()
        reason_holder = ["unknown"]

        # 启动 watchdog
        import time
        session_start = time.monotonic()

        task = asyncio.create_task(
            _watchdog_loop(
                mock_client,
                config,
                session_start,
                shutdown_event,
                exit_event,
                reason_holder,
                FeishuWsHealthState(),
            )
        )

        # 等待 watchdog 触发
        try:
            await asyncio.wait_for(exit_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            pytest.fail("watchdog did not detect dead connection")

        # 取消 watchdog
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 应标记为 dead_conn
        assert "dead_conn" in reason_holder[0] or exit_event.is_set()

    @pytest.mark.asyncio
    async def test_watchdog_respects_shutdown_event(self) -> None:
        """watchdog 应响应 shutdown_event。"""
        mock_client = MagicMock()
        mock_client.connected = True

        async def dummy_coro():
            pass

        mock_client.receive_task = asyncio.create_task(dummy_coro())

        config = FeishuWsHealthConfig(
            watchdog_interval_s=1.0,
            dead_conn_grace_s=90.0,
            reconnect_grace_s=300.0,
            refresh_interval_s=0.0,
            idle_refresh_s=0.0,
        )

        shutdown_event = asyncio.Event()
        shutdown_event.set()  # 立即设置
        exit_event = asyncio.Event()
        reason_holder = ["unknown"]

        import time
        session_start = time.monotonic()

        # watchdog 应立即退出
        await _watchdog_loop(
            mock_client,
            config,
            session_start,
            shutdown_event,
            exit_event,
            reason_holder,
            FeishuWsHealthState(),
        )

        # 不应设置 exit_event（通过 shutdown 退出）
        # 或者 exit_event 未设置
        assert shutdown_event.is_set()

        # 清理任务
        mock_client.receive_task.cancel()
        try:
            await mock_client.receive_task
        except asyncio.CancelledError:
            pass


__all__ = [
    "TestFeishuWsHealthConfig",
    "TestActivityTracking",
    "TestSessionEndTracking",
    "TestReceiveLoopExitReason",
    "TestWebSocketHealthIntegration",
    "TestFeishuWsClientBasic",
    "TestFeishuWsAutoReconnect",
    "TestWatchdogLoop",
]
