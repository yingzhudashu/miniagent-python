"""Unified Mock Strategies for MiniAgent Tests.

Provides standardized mock helpers for:
- LLM responses (tool calls, text, errors, streaming)
- Feishu WebSocket and API responses
- Tool execution results

This module consolidates patterns from llm_helpers.py and executor_helpers.py
into reusable mock strategies.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================================
# LLM Response Mocker
# ============================================================================


class LLMResponseMocker:
    """标准化 LLM 响应 mock 工厂。

    提供多种 LLM 响应类型的 mock 方法：
    - 工具调用响应
    - 文本响应
    - 错误响应
    - 流式响应

    Example:
        mocker = LLMResponseMocker()
        client = mocker.create_client()

        # 模拟工具调用
        mocker.setup_tool_call(client, "read_file", '{"path": "/test.txt"}')

        # 模拟文本响应
        mocker.setup_text_response(client, "Task completed")
    """

    def __init__(self) -> None:
        """初始化 mocker。"""
        self._call_counts: dict[str, int] = {}

    def create_client(self) -> MagicMock:
        """创建基础 mock 客户端。

        Returns:
            MagicMock 客户端，包含 chat.completions.create (AsyncMock)
        """
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock()
        return mock_client

    def _make_chunk(
        self,
        content: str | None = None,
        tool_calls: list[Any] | None = None,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> Any:
        """创建流式响应 chunk。

        Args:
            content: 文本内容
            tool_calls: 工具调用列表
            finish_reason: 结束原因
            usage: token 使用统计

        Returns:
            模拟的响应 chunk
        """
        delta = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
        )
        chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
            usage=usage,
        )
        return chunk

    def _make_tool_call(
        self,
        tool_name: str,
        tool_args: str | dict[str, Any],
        call_id: str = "call_1",
        index: int = 0,
    ) -> Any:
        """创建工具调用对象。

        Args:
            tool_name: 工具名称
            tool_args: 工具参数（字符串或字典）
            call_id: 调用 ID
            index: 调用索引

        Returns:
            模拟的工具调用对象
        """
        args_str = tool_args if isinstance(tool_args, str) else json.dumps(tool_args)
        return SimpleNamespace(
            index=index,
            id=call_id,
            function=SimpleNamespace(name=tool_name, arguments=args_str),
        )

    def setup_tool_call(
        self,
        client: MagicMock,
        tool_name: str,
        tool_args: str | dict[str, Any] = "{}",
        final_text: str = "done",
        *,
        call_id: str = "call_1",
        extra_chunks: list[Any] | None = None,
    ) -> MagicMock:
        """配置客户端返回工具调用后文本响应。

        Args:
            client: mock 客户端
            tool_name: 工具名称
            tool_args: 工具参数
            final_text: 最终文本
            call_id: 调用 ID
            extra_chunks: 额外 chunk 列表

        Returns:
            配置后的客户端
        """
        chunks = list(extra_chunks or [])

        # 工具调用 chunk
        async def tool_stream():
            tool_call = self._make_tool_call(tool_name, tool_args, call_id)
            yield self._make_chunk(content=None, tool_calls=[tool_call])

        # 文本响应 chunk
        async def text_stream():
            yield self._make_chunk(content=final_text, finish_reason="stop")

        if not chunks:
            chunks = [tool_stream, text_stream]

        call_count = {"n": 0}

        async def create_side_effect(*_a: object, **_k: object) -> Any:
            idx = call_count["n"]
            call_count["n"] += 1
            if idx < len(chunks):
                return chunks[idx]()
            return text_stream()

        client.chat.completions.create = AsyncMock(side_effect=create_side_effect)
        client._call_count = call_count  # type: ignore[attr-defined]
        return client

    def setup_text_response(
        self,
        client: MagicMock,
        text: str = "Task completed",
        *,
        finish_reason: str = "stop",
        usage: dict[str, int] | None = None,
    ) -> MagicMock:
        """配置客户端返回纯文本响应。

        Args:
            client: mock 客户端
            text: 文本内容
            finish_reason: 结束原因
            usage: token 使用统计

        Returns:
            配置后的客户端
        """
        async def text_stream():
            yield self._make_chunk(content=text, finish_reason=finish_reason, usage=usage)

        client.chat.completions.create = AsyncMock(return_value=text_stream())
        return client

    def setup_error_response(
        self,
        client: MagicMock,
        error_type: str = "api_error",
        error_message: str = "API call failed",
    ) -> MagicMock:
        """配置客户端抛出错误。

        Args:
            client: mock 宧户端
            error_type: 错误类型（api_error, timeout, rate_limit）
            error_message: 错误消息

        Returns:
            配置后的客户端
        """
        from openai import APIError, APIConnectionError, RateLimitError

        error_map = {
            "api_error": APIError(error_message),
            "timeout": APIConnectionError(error_message),
            "rate_limit": RateLimitError(error_message),
        }
        error = error_map.get(error_type, APIError(error_message))
        client.chat.completions.create = AsyncMock(side_effect=error)
        return client

    def setup_multi_tool_calls(
        self,
        client: MagicMock,
        tool_calls: list[dict[str, Any]],
        final_text: str = "All tools executed",
    ) -> MagicMock:
        """配置客户端返回多个工具调用。

        Args:
            client: mock 客户端
            tool_calls: 工具调用列表 [{"name": "...", "args": {...}}, ...]
            final_text: 最终文本

        Returns:
            配置后的客户端
        """
        async def multi_tool_stream():
            # 所有工具调用在一个 chunk 中
            calls = [
                self._make_tool_call(tc["name"], tc.get("args", {}), f"call_{i}", i)
                for i, tc in enumerate(tool_calls)
            ]
            yield self._make_chunk(content=None, tool_calls=calls)

        async def text_stream():
            yield self._make_chunk(content=final_text, finish_reason="stop")

        call_count = {"n": 0}

        async def create_side_effect(*_a: object, **_k: object) -> Any:
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 0:
                return multi_tool_stream()
            return text_stream()

        client.chat.completions.create = AsyncMock(side_effect=create_side_effect)
        client._call_count = call_count  # type: ignore[attr-defined]
        return client

    def setup_streaming_text(
        self,
        client: MagicMock,
        chunks: list[str],
        *,
        finish_reason: str = "stop",
    ) -> MagicMock:
        """配置客户端返回流式文本（多个 chunk）。

        Args:
            client: mock 客户端
            chunks: 文本 chunk 列表
            finish_reason: 结束原因

        Returns:
            配置后的客户端
        """
        async def text_stream():
            for i, chunk_text in enumerate(chunks):
                fr = finish_reason if i == len(chunks) - 1 else None
                yield self._make_chunk(content=chunk_text, finish_reason=fr)

        client.chat.completions.create = AsyncMock(return_value=text_stream())
        return client

    @contextlib.contextmanager
    def mock_all_llm_clients(self, client: MagicMock | None = None):
        """Mock 所有模块中的 get_shared_async_openai 引用。

        Args:
            client: 可选的 mock 客户端；默认创建新客户端

        Yields:
            mock 客户端
        """
        if client is None:
            client = self.create_client()

        patches = [
            patch("miniagent.core.openai_client.get_shared_async_openai", return_value=client),
            patch("miniagent.core.task_classifier.get_shared_async_openai", return_value=client),
            patch("miniagent.core.planner.get_shared_async_openai", return_value=client),
            patch("miniagent.core.executor.get_shared_async_openai", return_value=client),
        ]
        for p in patches:
            p.start()
        try:
            yield client
        finally:
            for p in patches:
                p.stop()


# ============================================================================
# Feishu Mocker
# ============================================================================


class FeishuMocker:
    """标准化飞书 API mock 工厂。

    提供飞书 WebSocket 和 API 响应的 mock 方法：
    - WebSocket 消息
    - 卡片操作
    - IM 回复

    Example:
        mocker = FeishuMocker()
        ws = mocker.create_websocket()
        mocker.setup_message_receive(ws, "text", {"content": "Hello"})
    """

    def __init__(self) -> None:
        """初始化 mocker。"""
        pass

    def create_websocket(self) -> MagicMock:
        """创建飞书 WebSocket mock。

        Returns:
            MagicMock WebSocket 客户端
        """
        ws = MagicMock()
        ws.connect = AsyncMock()
        ws.disconnect = AsyncMock()
        ws.send = AsyncMock()
        ws.recv = AsyncMock()
        ws.is_connected = False
        return ws

    def setup_message_receive(
        self,
        ws: MagicMock,
        event_type: str,
        payload: dict[str, Any],
    ) -> MagicMock:
        """配置 WebSocket 接收消息。

        Args:
            ws: WebSocket mock
            event_type: 事件类型（text, image, file, post）
            payload: 消息内容

        Returns:
            配置后的 WebSocket
        """
        message = json.dumps({
            "type": event_type,
            "data": payload,
        })

        async def recv_side_effect():
            return message

        ws.recv = AsyncMock(side_effect=recv_side_effect)
        return ws

    def setup_websocket_connected(self, ws: MagicMock) -> MagicMock:
        """配置 WebSocket 已连接状态。

        Args:
            ws: WebSocket mock

        Returns:
            配置后的 WebSocket
        """
        ws.is_connected = True
        ws.connect = AsyncMock(return_value=None)
        return ws

    def setup_websocket_disconnect(self, ws: MagicMock) -> MagicMock:
        """配置 WebSocket 断开。

        Args:
            ws: WebSocket mock

        Returns:
            配置后的 WebSocket
        """
        ws.is_connected = False
        ws.disconnect = AsyncMock(return_value=None)
        return ws

    def create_card_action(
        self,
        action_value: str,
        card_id: str = "card_123",
    ) -> dict[str, Any]:
        """创建卡片动作响应。

        Args:
            action_value: 动作值
            card_id: 卡片 ID

        Returns:
            卡片动作字典
        """
        return {
            "action": {
                "value": action_value,
            },
            "card": {
                "card_id": card_id,
            },
        }

    def create_im_reply(
        self,
        message_id: str,
        content: str,
        msg_type: str = "text",
    ) -> dict[str, Any]:
        """创建 IM 回复响应。

        Args:
            message_id: 消息 ID
            content: 内容
            msg_type: 消息类型

        Returns:
            IM 回复字典
        """
        return {
            "message_id": message_id,
            "content": json.dumps({"text": content}) if msg_type == "text" else content,
            "msg_type": msg_type,
        }

    def create_feishu_client(self) -> MagicMock:
        """创建飞书 Lark 客户端 mock。

        Returns:
            MagicMock 客户端
        """
        client = MagicMock()
        client.im.message.create = AsyncMock()
        client.im.message.reply = AsyncMock()
        client.im.message.update = AsyncMock()
        client.im.message.delete = AsyncMock()
        client.docx.document.create = AsyncMock()
        client.docx.document.get = AsyncMock()
        client.bitable.app.table.list = AsyncMock()
        return client

    def setup_im_send_success(
        self,
        client: MagicMock,
        message_id: str = "msg_123",
    ) -> MagicMock:
        """配置 IM 发送成功响应。

        Args:
            client: 飞书客户端 mock
            message_id: 返回的消息 ID

        Returns:
            配置后的客户端
        """
        response = SimpleNamespace(data=SimpleNamespace(message_id=message_id))
        client.im.message.create = AsyncMock(return_value=response)
        client.im.message.reply = AsyncMock(return_value=response)
        return client


# ============================================================================
# Tool Mocker
# ============================================================================


class ToolMocker:
    """标准化工具执行 mock 工厂。

    提供工具执行结果的 mock 方法：
    - 文件系统操作
    - 命令执行
    - 工具成功/失败结果

    Example:
        mocker = ToolMocker()
        result = mocker.success_result("File read successfully")
        result = mocker.error_result("Permission denied")
    """

    def __init__(self) -> None:
        """初始化 mocker。"""
        pass

    def success_result(
        self,
        content: str = "Success",
        meta: dict[str, Any] | None = None,
    ) -> Any:
        """创建工具成功结果。

        Args:
            content: 内容
            meta: 元数据

        Returns:
            ToolResult 对象
        """
        from miniagent.types.tool import ToolResult

        return ToolResult(
            success=True,
            content=content,
            meta=meta or {},
        )

    def error_result(
        self,
        error: str = "Error occurred",
        *,
        is_user_error: bool = True,
    ) -> Any:
        """创建工具错误结果。

        Args:
            error: 错误消息
            is_user_error: 是否为用户错误

        Returns:
            ToolResult 对象
        """
        from miniagent.types.tool import ToolResult

        return ToolResult(
            success=False,
            content=error,
            meta={"is_user_error": is_user_error},
        )

    def create_handler(
        self,
        result: Any,
    ) -> Any:
        """创建工具 handler mock。

        Args:
            result: 工具结果

        Returns:
            AsyncMock handler
        """
        async def handler(args: dict, ctx: Any) -> Any:
            return result

        return handler

    def create_filesystem_tool(
        self,
        tool_name: str,
        result: Any,
    ) -> Any:
        """创建文件系统工具定义。

        Args:
            tool_name: 工具名称
            result: 执行结果

        Returns:
            ToolDefinition 对象
        """
        from miniagent.types.tool import ToolDefinition

        schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Mock {tool_name} tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                    },
                    "required": ["path"],
                },
            },
        }

        return ToolDefinition(
            schema=schema,
            handler=self.create_handler(result),
            permission="allowlist",
            help_text=f"Mock {tool_name}",
            toolbox="filesystem",
        )

    def create_exec_tool(
        self,
        result: Any,
    ) -> Any:
        """创建命令执行工具定义。

        Args:
            result: 执行结果

        Returns:
            ToolDefinition 对象
        """
        from miniagent.types.tool import ToolDefinition

        schema = {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute"},
                    },
                    "required": ["command"],
                },
            },
        }

        return ToolDefinition(
            schema=schema,
            handler=self.create_handler(result),
            permission="allowlist",
            help_text="Execute shell command",
            toolbox="exec",
        )

    def create_ping_tool(self) -> Any:
        """创建基础 ping 工具定义。

        Returns:
            ToolDefinition 对象
        """
        return self.create_filesystem_tool("ping_tool", self.success_result("pong"))


# ============================================================================
# Convenience Functions
# ============================================================================


def create_mock_llm_client() -> MagicMock:
    """创建基础 mock LLM 客户端（便捷函数）。

    Returns:
        MagicMock 客户端
    """
    return LLMResponseMocker().create_client()


def mock_all_llm_clients(client: MagicMock | None = None):
    """Mock 所有 LLM 客户端引用（便捷函数，上下文管理器）。

    Args:
        client: 可选的 mock 客户端

    Yields:
        mock 客户端
    """
    mocker = LLMResponseMocker()
    return mocker.mock_all_llm_clients(client)


def make_ping_tool_registry() -> tuple[Any, Any]:
    """创建 ping 工具注册表对（便捷函数）。

    返回：(main_registry, session_registry)

    保持与 executor_helpers.py 兼容。
    """
    from miniagent.infrastructure.registry import DefaultToolRegistry

    tool_mocker = ToolMocker()
    ping_tool = tool_mocker.create_ping_tool()

    main = DefaultToolRegistry()
    sess = DefaultToolRegistry()

    sess.register("ping_tool", ping_tool)
    return main, sess


def mock_memory_bundle() -> tuple[MagicMock, MagicMock, MagicMock]:
    """创建记忆系统 mock 三元组（便捷函数）。

    返回：(memory_store, activity_log, keyword_index)

    保持与 executor_helpers.py 兼容。
    """
    ms = MagicMock()
    al = MagicMock()
    ki = MagicMock()
    ki.get_stats.return_value = {"total_keywords": 0}
    return ms, al, ki


__all__ = [
    # Mocker classes
    "LLMResponseMocker",
    "FeishuMocker",
    "ToolMocker",
    # Convenience functions
    "create_mock_llm_client",
    "mock_all_llm_clients",
    "make_ping_tool_registry",
    "mock_memory_bundle",
]