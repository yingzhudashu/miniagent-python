"""miniagent/compat.py 的单元测试。

compat 模块是聚合导出层，将已拆分的子包符号聚合为单一导入入口。
本测试验证：
- 所有 __all__ 导出符号均可 import
- COMPAT_ASYNC_EXPORTS 与真实 async 签名一致
- unified_entry() 在 mock asyncio.run 下能构造 RuntimeContext 而不进入完整主循环
- agent.queue_mode 非法值会记录 warning 并回落到 queue
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from miniagent.infrastructure.message_queue import QueueMode
from miniagent.runtime.context import RuntimeContext


class TestCompatExports:
    """验证 compat.__all__ 中的所有符号均可导入。"""

    def test_all_exports_importable(self) -> None:
        from miniagent import compat

        for name in compat.__all__:
            assert hasattr(compat, name), f"compat.__all__ lists '{name}' but it is not exported"

    def test_unified_entry_exists(self) -> None:
        from miniagent.compat import unified_entry

        assert callable(unified_entry)

    def test_runtime_context_importable(self) -> None:
        from miniagent.compat import RuntimeContext

        assert RuntimeContext is not None

    def test_engine_importable(self) -> None:
        from miniagent.compat import UnifiedEngine

        assert UnifiedEngine is not None

    def test_cli_commands_importable(self) -> None:
        from miniagent.compat import (
            cmd_help,
            cmd_session_list,
            cmd_session_switch,
        )

        assert callable(cmd_help)
        assert callable(cmd_session_list)
        assert callable(cmd_session_switch)

    def test_compat_async_exports_match_signatures(self) -> None:
        from miniagent import compat

        for name in compat.COMPAT_ASYNC_EXPORTS:
            assert name in compat.__all__, f"COMPAT_ASYNC_EXPORTS 含未导出符号: {name}"
            obj = getattr(compat, name)
            assert asyncio.iscoroutinefunction(obj), f"{name} 应标注为 async"

        skip_async_check = compat.COMPAT_ASYNC_EXPORTS | {
            "RuntimeContext",
            "FeishuRuntime",
            "ThinkingDisplay",
            "UnifiedEngine",
            "unified_entry",
        }
        for name in compat.__all__:
            if name in skip_async_check:
                continue
            obj = getattr(compat, name)
            assert not asyncio.iscoroutinefunction(obj), f"{name} 不应标注为 async"


class TestUnifiedEntry:
    """unified_entry 启动编排（隔离 asyncio.run / LLM）。"""

    @staticmethod
    def _run_unified_entry_with_mock_loop(*, queue_mode: str | None = None) -> RuntimeContext:
        captured: dict[str, RuntimeContext] = {}

        async def fake_unified_main(ctx: RuntimeContext) -> None:
            captured["ctx"] = ctx

        def fake_asyncio_run(coro):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

        config_overrides: dict[str, str] = {}
        if queue_mode is not None:
            config_overrides["agent.queue_mode"] = queue_mode

        from miniagent.infrastructure import json_config as json_config_module

        real_get_config = json_config_module.get_config

        def fake_get_config(key: str, default=None):
            if key in config_overrides:
                return config_overrides[key]
            return real_get_config(key, default)

        with (
            patch("miniagent.compat.unified_main", fake_unified_main),
            patch("asyncio.run", fake_asyncio_run),
            patch(
                "miniagent.infrastructure.env_loader.load_secrets_from_project_root",
            ),
            patch.object(json_config_module, "get_config", side_effect=fake_get_config),
        ):
            from miniagent.compat import unified_entry

            unified_entry()

        assert "ctx" in captured, "unified_entry 未调用 unified_main(ctx)"
        return captured["ctx"]

    def test_unified_entry_builds_runtime_context(
        self,
        state_dir: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-compat-entry")
        ctx = self._run_unified_entry_with_mock_loop()
        assert isinstance(ctx, RuntimeContext)
        assert ctx.registry is not None
        assert ctx.monitor is not None
        assert ctx.engine is not None
        assert ctx.message_queue is not None
        assert ctx.channel_router is not None
        assert ctx.feishu is not None
        assert ctx.memory_store is not None
        assert ctx.activity_log is not None
        assert ctx.keyword_index is not None
        assert ctx.openai_client is not None

    def test_unified_entry_preemptive_queue_mode(
        self,
        state_dir: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-compat-entry")
        ctx = self._run_unified_entry_with_mock_loop(queue_mode="preemptive")
        assert ctx.message_queue.mode == QueueMode.PREEMPTIVE

    def test_unified_entry_invalid_queue_mode_warns_and_falls_back(
        self,
        state_dir: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-compat-entry")
        with patch("miniagent.compat._logger") as mock_logger:
            ctx = self._run_unified_entry_with_mock_loop(queue_mode="invalid-mode")
        assert ctx.message_queue.mode == QueueMode.QUEUE
        mock_logger.warning.assert_called_once()
        assert "invalid-mode" in str(mock_logger.warning.call_args)
