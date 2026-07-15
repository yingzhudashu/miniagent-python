"""Test configuration for miniagent-python.

Enhanced fixtures for standardized testing infrastructure.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine.cli_state import CliLoopState

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# Basic Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _isolate_json_config_from_user_file(request: pytest.FixtureRequest, tmp_path):
    """Keep unit tests independent from the developer's config.user.json."""
    if request.node.get_closest_marker("evaluation") is not None:
        yield
        return

    import json

    from miniagent.assistant.infrastructure import json_config

    previous = json_config._config_loader
    user_path = tmp_path / "default-user-config.json"
    user_path.write_text(json.dumps({}), encoding="utf-8")
    json_config.install_config_loader(
        json_config.JsonConfigLoader(defaults_path=None, user_path=str(user_path))
    )
    try:
        yield
    finally:
        json_config.install_config_loader(previous)


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

    from miniagent.assistant.infrastructure.json_config import (
        JsonConfigLoader,
        install_config_loader,
    )

    def _factory(user_overrides: dict | None = None) -> JsonConfigLoader:
        user_path = tmp_path / "config.user.json"
        user_path.write_text(json.dumps(user_overrides or {}), encoding="utf-8")
        loader = JsonConfigLoader(
            defaults_path=None,
            user_path=str(user_path),
        )
        install_config_loader(loader)
        return loader

    return _factory


# ============================================================================
# Process Singleton Reset
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_process_singletons_after_test() -> None:
    """Teardown：重置仍由模块管理的共享客户端与纯性能缓存。

    包含：
    - AsyncOpenAI 客户端
    - Executor 环境缓存
    - LoopDetector 参数缓存
    - InstanceRegistry（如果已初始化）
    """
    from miniagent.agent.executor import _reset_env_caches_for_tests
    from miniagent.agent.loop_detector import clear_args_cache

    yield

    # 重置所有进程级单例
    _reset_env_caches_for_tests()
    clear_args_cache()

    # 尝试重置 InstanceRegistry（如果存在）
    try:
        from miniagent.assistant.infrastructure.instance import reset_instance_registry_for_tests
        reset_instance_registry_for_tests()
    except ImportError:
        pass


# ============================================================================
# Mock OpenAI Client Fixtures
# ============================================================================


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
    from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry

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
    from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry

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
def memory_runtime(state_dir: str):
    """Return a real, isolated memory object graph owned by the requesting test."""
    from miniagent.assistant.memory.runtime import create_memory_runtime

    runtime = create_memory_runtime(state_dir)
    yield runtime
    runtime.close()


@pytest.fixture
def knowledge_registry():
    """Return an empty explicitly injected knowledge registry test double."""
    from tests.memory_helpers import make_knowledge_registry

    return make_knowledge_registry()


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
    from miniagent.assistant.memory.keyword_index import KeywordIndex
    from miniagent.assistant.memory.shared_registry import MemoryEntryRegistry

    registry = MemoryEntryRegistry(state_dir=state_dir)
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
    from miniagent.agent.types.config import AgentConfig, SessionBindingConfig

    _, sess = mock_tool_registry_pair
    return AgentConfig(
        max_turns=3,
        allow_parallel_tools=True,
        tool_selection_strategy="all",
        session_config=SessionBindingConfig(session_registry=sess),
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
def mock_cli_state() -> CliLoopState:
    """CLI 循环状态 mock（与 ``CliLoopState`` 键对齐）。"""
    ctx = MagicMock()
    ctx.message_queue = MagicMock()
    ctx.channel_router = MagicMock()
    return {
        "active_session_id": "test_session",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }


# ============================================================================
# Session Fixtures
# ============================================================================


@pytest.fixture
def mock_session_manager(state_dir: str) -> Any:
    """会话管理器 mock（使用隔离状态目录）。

    返回 DefaultSessionManager 实例。
    """
    from miniagent.assistant.session.manager import DefaultSessionManager

    return DefaultSessionManager(state_dir=state_dir)


# ============================================================================
# Empty Plan Fixture
# ============================================================================


@pytest.fixture
def empty_plan() -> Any:
    """空规划结构。

    返回 StructuredPlan 实例。
    """
    from miniagent.agent.types.planning import StructuredPlan

    return StructuredPlan(summary="empty plan", steps=[], required_toolboxes=[])


__all__ = [
    # Basic
    "state_dir",
    # Singleton Reset
    "_reset_process_singletons_after_test",
    # OpenAI Mock
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
