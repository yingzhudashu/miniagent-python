"""Test configuration for miniagent-python.

Enhanced fixtures for standardized testing infrastructure.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# Basic Fixtures
# ============================================================================


@pytest.fixture()
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Isolated state directory via MINIAGENT_PATHS_STATE_DIR env override.

    使用环境变量而非配置文件，避免 resolve_state_dir() 添加 projects/{key} 后缀。
    """
    d = str(tmp_path / "state")
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", d)
    return d


@pytest.fixture()
def isolated_config_loader(tmp_path):
    """Factory: build JsonConfigLoader with optional user overrides dict."""
    import json

    from miniagent.infrastructure.json_config import JsonConfigLoader

    def _factory(user_overrides: dict | None = None) -> JsonConfigLoader:
        user_path = tmp_path / "config.user.json"
        user_path.write_text(json.dumps(user_overrides or {}), encoding="utf-8")
        loader = JsonConfigLoader(
            defaults_path=os.path.join(PROJECT_ROOT, "config.defaults.json"),
            user_path=str(user_path),
        )
        JsonConfigLoader._instance = loader
        return loader

    return _factory


# ============================================================================
# Process Singleton Reset
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_process_singletons_after_test() -> None:
    """Teardown：重置进程级默认记忆 bundle 与共享 AsyncOpenAI，减轻测试顺序敏感。

    包含：
    - 记忆三元组 (memory_store, activity_log, keyword_index)
    - AsyncOpenAI 客户端
    - Executor 环境缓存
    - LoopDetector 参数缓存
    - RuntimeContext（如果已初始化）
    - ChannelRouter（如果已初始化）
    - InstanceRegistry（如果已初始化）
    """
    from miniagent.core.executor import _reset_env_caches_for_tests
    from miniagent.core.openai_client import reset_shared_async_openai_for_tests
    from miniagent.infrastructure.loop_detector import clear_args_cache
    from miniagent.memory.defaults import reset_process_default_memory_bundle_for_tests

    yield

    # 重置所有进程级单例
    reset_process_default_memory_bundle_for_tests()
    reset_shared_async_openai_for_tests()
    _reset_env_caches_for_tests()
    clear_args_cache()

    # 尝试重置 RuntimeContext（如果存在）
    try:
        from miniagent.runtime.context import reset_runtime_context_for_tests
        reset_runtime_context_for_tests()
    except ImportError:
        pass

    # 尝试重置 ChannelRouter（如果存在）
    try:
        from miniagent.infrastructure.channel_router import reset_channel_router_for_tests
        reset_channel_router_for_tests()
    except ImportError:
        pass

    # 尝试重置 InstanceRegistry（如果存在）
    try:
        from miniagent.infrastructure.instance import reset_instance_registry_for_tests
        reset_instance_registry_for_tests()
    except ImportError:
        pass


# ============================================================================
# Mock OpenAI Client Fixtures
# ============================================================================


@pytest.fixture
def mock_openai_client() -> MagicMock:
    """全局 OpenAI 客户端 mock，避免真实 API 调用。

    返回一个 MagicMock 客户端，包含：
    - chat.completions.create（AsyncMock）

    使用方式：
        def test_example(mock_openai_client):
            mock_openai_client.chat.completions.create.return_value = ...
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()
    return mock_client


@pytest.fixture
def mock_llm_context(mock_openai_client: MagicMock):
    """Mock 所有模块中的 get_shared_async_openai 引用（上下文管理器模式）。

    包括：
    - miniagent.core.openai_client
    - miniagent.core.task_classifier
    - miniagent.core.planner
    - miniagent.core.executor

    使用方式：
        def test_example(mock_llm_context):
            mock_client = mock_llm_context.__enter__()
            # ... 使用 mock_client
            mock_llm_context.__exit__(None, None, None)
    """
    patches = [
        patch("miniagent.core.openai_client.get_shared_async_openai", return_value=mock_openai_client),
        patch("miniagent.core.task_classifier.get_shared_async_openai", return_value=mock_openai_client),
        patch("miniagent.core.planner.get_shared_async_openai", return_value=mock_openai_client),
        patch("miniagent.core.executor.get_shared_async_openai", return_value=mock_openai_client),
    ]

    @contextlib.contextmanager
    def _ctx():
        for p in patches:
            p.start()
        try:
            yield mock_openai_client
        finally:
            for p in patches:
                p.stop()

    return _ctx()


# ============================================================================
# Mock Feishu Fixtures
# ============================================================================


@pytest.fixture
def mock_feishu_websocket() -> MagicMock:
    """飞书 WebSocket mock，模拟消息接收。

    返回一个 MagicMock WebSocket 客户端，包含：
    - connect（AsyncMock）
    - disconnect（AsyncMock）
    - send（AsyncMock）
    - recv（AsyncMock）
    - is_connected（返回 False）
    """
    mock_ws = MagicMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock()
    mock_ws.is_connected = False
    return mock_ws


@pytest.fixture
def mock_feishu_config(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """飞书配置 mock，避免真实飞书连接。

    设置环境变量：
    - FEISHU_APP_ID
    - FEISHU_APP_SECRET

    返回配置字典。
    """
    config = {
        "FEISHU_APP_ID": "test_app_id",
        "FEISHU_APP_SECRET": "test_app_secret",
    }
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    return config


# ============================================================================
# Workspace Fixtures
# ============================================================================


@pytest.fixture
def isolated_workspace() -> str:
    """隔离工作空间目录，测试完成后自动清理。

    创建临时目录，适合文件操作测试。
    """
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def workspace_with_files(isolated_workspace: str) -> dict[str, str]:
    """带预置文件的隔离工作空间。

    创建以下文件：
    - test.txt: "Hello, World!"
    - data.json: {"key": "value"}

    返回文件路径字典。
    """
    import json

    files = {}
    txt_path = os.path.join(isolated_workspace, "test.txt")
    json_path = os.path.join(isolated_workspace, "data.json")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("Hello, World!")
    files["test.txt"] = txt_path

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"key": "value"}, f)
    files["data.json"] = json_path

    return files


# ============================================================================
# Tool Registry Fixtures
# ============================================================================


@pytest.fixture
def mock_tool_registry() -> Any:
    """预填充的工具注册表，包含基础测试工具。

    注册工具：
    - ping_tool: 返回 ToolResult(True, "ok")

    返回 DefaultToolRegistry 实例。
    """
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

    registry = DefaultToolRegistry()

    async def ping_handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(True, "ok")

    ping_schema = {
        "type": "function",
        "function": {
            "name": "ping_tool",
            "description": "Test ping tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    registry.register(
        "ping_tool",
        ToolDefinition(
            schema=ping_schema,
            handler=ping_handler,
            permission="allowlist",
            help_text="Test ping tool",
            toolbox=None,
        ),
    )

    return registry


@pytest.fixture
def mock_tool_registry_pair() -> tuple[Any, Any]:
    """主/会话双工具注册表（用于 execute_plan 测试）。

    返回：(main_registry, session_registry)
    """
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

    main = DefaultToolRegistry()
    sess = DefaultToolRegistry()

    async def ping_handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(True, "ok")

    ping_schema = {
        "type": "function",
        "function": {
            "name": "ping_tool",
            "description": "Test ping tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    sess.register(
        "ping_tool",
        ToolDefinition(
            schema=ping_schema,
            handler=ping_handler,
            permission="allowlist",
            help_text="Test ping tool",
            toolbox=None,
        ),
    )

    return main, sess


# ============================================================================
# Memory Bundle Fixtures
# ============================================================================


@pytest.fixture
def mock_memory_bundle() -> tuple[MagicMock, MagicMock, MagicMock]:
    """记忆系统 mock 三元组。

    返回：(memory_store, activity_log, keyword_index)
    """
    ms = MagicMock()
    al = MagicMock()
    ki = MagicMock()
    ki.get_stats.return_value = {"total_keywords": 0}
    return ms, al, ki


@pytest.fixture
def mock_keyword_index(state_dir: str) -> Any:
    """真实关键词索引实例（使用隔离状态目录）。

    返回 KeywordIndex 实例。
    """
    from miniagent.memory.keyword_index import KeywordIndex
    from miniagent.memory.shared_registry import get_registry, reset_registry

    reset_registry()
    registry = get_registry(state_dir)
    ki = KeywordIndex(state_dir=state_dir, registry=registry)
    return ki


# ============================================================================
# Time Fixtures
# ============================================================================


@pytest.fixture
def frozen_time():
    """冻结时间，避免时间依赖。

    使用 freezegun（如已安装）冻结到 2024-01-01 12:00:00。

    如 freezegun 未安装，返回 None。
    """
    try:
        import freezegun

        with freezegun.freeze_time("2024-01-01 12:00:00"):
            yield "2024-01-01 12:00:00"
    except ImportError:
        yield None


# ============================================================================
# Agent Config Fixtures
# ============================================================================


@pytest.fixture
def mock_agent_config(mock_tool_registry_pair: tuple[Any, Any]) -> Any:
    """标准 Agent 配置（用于 execute_plan 测试）。

    返回 AgentConfig 实例。
    """
    from miniagent.types.config import AgentConfig

    _, sess = mock_tool_registry_pair
    return AgentConfig(
        max_turns=3,
        session_key=None,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_registry=sess,
    )


# ============================================================================
# Streaming Client Mock
# ============================================================================


@pytest.fixture
def mock_streaming_client_factory():
    """创建流式响应 mock 客户端的工厂函数。

    返回一个函数，接受参数：
    - tool_name: 工具名称（默认 "ping_tool"）
    - tool_args: 工具参数 JSON（默认 "{}"）
    - final_text: 最终文本（默认 "done"）
    - extra_streams: 额外流响应列表

    使用方式：
        def test_example(mock_streaming_client_factory):
            client = mock_streaming_client_factory(
                tool_name="my_tool",
                final_text="completed"
            )
    """
    from types import SimpleNamespace

    def _factory(
        *,
        tool_name: str = "ping_tool",
        tool_args: str = "{}",
        final_text: str = "done",
        extra_streams: list[Any] | None = None,
    ) -> MagicMock:
        mock_client = MagicMock()

        class _Chunk:
            def __init__(self, delta: Any, usage: Any = None) -> None:
                self.choices = [SimpleNamespace(delta=delta)]
                self.usage = usage

        streams = list(extra_streams or [])

        async def default_tool_stream():
            delta = SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="call_1",
                        function=SimpleNamespace(name=tool_name, arguments=tool_args),
                    )
                ],
            )
            yield _Chunk(delta)

        async def default_text_stream():
            yield _Chunk(SimpleNamespace(content=final_text, tool_calls=None))

        if not streams:
            streams = [default_tool_stream, default_text_stream]

        call_count = {"n": 0}

        async def create_side_effect(*_a: object, **_k: object) -> Any:
            idx = call_count["n"]
            call_count["n"] += 1
            if idx < len(streams):
                return streams[idx]()
            return default_text_stream()

        mock_client.chat.completions.create = AsyncMock(side_effect=create_side_effect)
        mock_client._call_count = call_count  # type: ignore[attr-defined]
        return mock_client

    return _factory


# ============================================================================
# CLI State Fixtures
# ============================================================================


@pytest.fixture
def mock_cli_state() -> dict[str, Any]:
    """CLI 循环状态 mock。

    返回 CliLoopState TypedDict 的字典形式。
    """
    return {
        "current_session_id": "test_session",
        "session_list": [],
        "instance_id": 1,
        "feishu_enabled": False,
        "queue_mode": "preemptive",
        "background_tasks": {},
        "scheduled_tasks": {},
        "channel_bindings": {},
    }


# ============================================================================
# Session Fixtures
# ============================================================================


@pytest.fixture
def mock_session_manager(state_dir: str) -> Any:
    """会话管理器 mock（使用隔离状态目录）。

    返回 DefaultSessionManager 实例。
    """
    from miniagent.session.manager import DefaultSessionManager

    return DefaultSessionManager(state_dir=state_dir)


# ============================================================================
# Empty Plan Fixture
# ============================================================================


@pytest.fixture
def empty_plan() -> Any:
    """空规划结构。

    返回 StructuredPlan 实例。
    """
    from miniagent.types.planning import StructuredPlan

    return StructuredPlan(summary="empty plan", steps=[], required_toolboxes=[])


__all__ = [
    # Basic
    "state_dir",
    # Singleton Reset
    "_reset_process_singletons_after_test",
    # OpenAI Mock
    "mock_openai_client",
    "mock_llm_context",
    # Feishu Mock
    "mock_feishu_websocket",
    "mock_feishu_config",
    # Workspace
    "isolated_workspace",
    "workspace_with_files",
    # Tool Registry
    "mock_tool_registry",
    "mock_tool_registry_pair",
    # Memory
    "mock_memory_bundle",
    "mock_keyword_index",
    # Time
    "frozen_time",
    # Agent Config
    "mock_agent_config",
    # Streaming Client
    "mock_streaming_client_factory",
    # CLI State
    "mock_cli_state",
    # Session
    "mock_session_manager",
    # Plan
    "empty_plan",
]