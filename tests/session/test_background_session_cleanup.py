"""Tests for background session artifact cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine.bg_session_cleanup import (
    cleanup_background_session_artifacts,
    is_background_session_key,
)
from miniagent.assistant.infrastructure.trace_stats import remove_session_from_trace_files
from miniagent.assistant.memory.activity_log import ActivityLogger
from miniagent.assistant.memory.layered_memory import (
    load_agent_longterm,
    promote_to_agent_longterm,
    remove_agent_longterm_entries_for_session,
)
from miniagent.assistant.utils.session_id import safe_session_id


class TestIsBackgroundSessionKey:
    def test_bg_prefix(self):
        assert is_background_session_key("__bg__abc123") is True
        assert is_background_session_key("cli-session") is False


class TestCleanupBackgroundSessionArtifacts:
    @pytest.mark.asyncio
    async def test_removes_workspace_memory_and_activity_log(self, state_dir, memory_runtime):
        session_key = "__bg__deadbeef"
        safe_id = safe_session_id(session_key)

        workspace_dir = os.path.join(state_dir, "sessions", safe_id)
        os.makedirs(os.path.join(workspace_dir, "files"), exist_ok=True)
        with open(os.path.join(workspace_dir, "history.json"), "w", encoding="utf-8") as f:
            json.dump([{"role": "user", "content": "hi"}], f)

        memory_path = os.path.join(state_dir, "memory", f"{safe_id}.json")
        os.makedirs(os.path.dirname(memory_path), exist_ok=True)
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write('{"session_id":"x"}')

        session_lt = os.path.join(state_dir, "memory", "session_lt", f"{safe_id}.json")
        os.makedirs(os.path.dirname(session_lt), exist_ok=True)
        with open(session_lt, "w", encoding="utf-8") as f:
            f.write("{}")

        diary_dir = os.path.join(state_dir, "memory", "diary", safe_id)
        os.makedirs(diary_dir, exist_ok=True)
        with open(os.path.join(diary_dir, "2026-06-14.md"), "w", encoding="utf-8") as f:
            f.write("# diary")

        registry = memory_runtime.registry
        registry.register(
            session_key,
            type("E", (), {
                "timestamp": "2026-06-14T00:00:00+00:00",
                "user_snippet": "hello",
                "summary": "test",
                "facts": [],
            })(),
        )

        activity_log = memory_runtime.activity_log
        activity_log.log_session_start(session_key, "background prompt")

        await cleanup_background_session_artifacts(
            session_key,
            memory=memory_runtime,
        )

        assert not os.path.exists(workspace_dir)
        assert not os.path.exists(memory_path)
        assert not os.path.exists(session_lt)
        assert not os.path.isdir(diary_dir)
        assert registry.remove_session_entries(session_key) == []

        today_log = activity_log._get_today_path()
        if os.path.exists(today_log):
            assert session_key not in today_log.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_skips_non_background_session(self, state_dir):
        session_key = "cli-main"
        safe_id = safe_session_id(session_key)
        workspace_dir = os.path.join(state_dir, "sessions", safe_id)
        os.makedirs(workspace_dir, exist_ok=True)

        await cleanup_background_session_artifacts(session_key)

        assert os.path.isdir(workspace_dir)


class TestBackgroundTaskCleanupIntegration:
    @pytest.mark.asyncio
    async def test_execute_task_cleans_disk_after_completion(self, state_dir, memory_runtime):
        import asyncio
        from types import SimpleNamespace

        from miniagent.assistant.engine.background_tasks import BackgroundTaskManager

        manager = BackgroundTaskManager()
        session_key_holder: list[str] = []

        class MockEngine:
            async def run_agent_with_thinking(self, **kwargs):
                session_key = kwargs["session_key"]
                session_key_holder.append(session_key)
                safe_id = safe_session_id(session_key)
                workspace_dir = os.path.join(state_dir, "sessions", safe_id)
                os.makedirs(workspace_dir, exist_ok=True)
                with open(os.path.join(workspace_dir, "history.json"), "w", encoding="utf-8") as f:
                    json.dump([], f)
                await asyncio.sleep(0.05)
                return "done"

        engine = MockEngine()
        state = {
            "skill_toolboxes": [],
            "skill_prompts": None,
            "runtime_ctx": SimpleNamespace(memory=memory_runtime),
        }

        task_id = await manager.start_task(engine, "cleanup test", state)
        await asyncio.sleep(0.2)

        status = manager.get_status(task_id)
        assert status is not None
        assert status["status"] == "completed"

        result = await manager.get_result(task_id)
        assert result == "done"

        assert session_key_holder
        workspace_dir = os.path.join(state_dir, "sessions", safe_session_id(session_key_holder[0]))
        assert not os.path.exists(workspace_dir)


class TestActivityLogMultiDayCleanup:
    def test_remove_session_from_all_md_files(self, state_dir):
        session_key = "__bg__multiday"
        base = os.path.join(state_dir, "memory")
        os.makedirs(base, exist_ok=True)

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(base, f"{day}.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"---\n## {session_key} (cli)\n\n### 用户输入\n\nhello\n\n")

        ActivityLogger(base_dir=base).remove_session(session_key)

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(base, f"{day}.md")
            assert not os.path.exists(path) or session_key not in open(
                path, encoding="utf-8"
            ).read()


class TestTraceMultiFileCleanup:
    def test_remove_session_from_all_trace_shards(self, state_dir, isolated_config_loader):
        session_key = "__bg__trace01"
        trace_dir = os.path.join(state_dir, "logs")
        os.makedirs(trace_dir, exist_ok=True)
        isolated_config_loader({"trace": {"output_dir": trace_dir}})

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(trace_dir, f"trace-{day}.jsonl")
            lines = [
                json.dumps({"session_key": session_key, "type": "tool.start"}),
                json.dumps({"session_key": "cli-main", "type": "llm.request"}),
            ]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")

        assert remove_session_from_trace_files(session_key) == 2

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(trace_dir, f"trace-{day}.jsonl")
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            assert session_key not in content
            assert "cli-main" in content

    def test_remove_session_stream_preserves_malformed_and_unterminated_lines(
        self,
        state_dir,
        isolated_config_loader,
    ):
        trace_dir = os.path.join(state_dir, "logs")
        os.makedirs(trace_dir, exist_ok=True)
        isolated_config_loader({"trace": {"output_dir": trace_dir}})
        path = os.path.join(trace_dir, "trace-2026-06-13.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"session_key": "remove", "type": "x"}) + "\n")
            handle.write("{malformed}\n")
            handle.write(json.dumps({"session_key": "keep", "type": "y"}))

        assert remove_session_from_trace_files("remove") == 1
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        assert lines == [
            "{malformed}",
            json.dumps({"session_key": "keep", "type": "y"}),
        ]


class TestAgentLongTermCleanup:
    def test_remove_agent_longterm_entries_for_session(self, state_dir, monkeypatch):
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)
        promote_to_agent_longterm("keep me", source_session="cli-main")
        promote_to_agent_longterm("remove me", source_session="__bg__agentlt")

        assert remove_agent_longterm_entries_for_session("__bg__agentlt") == 1

        texts = [entry.get("text") for entry in load_agent_longterm().get("entries", [])]
        assert "remove me" not in texts
        assert "keep me" in texts

    @pytest.mark.asyncio
    async def test_cleanup_removes_agent_longterm_entries(self, state_dir, monkeypatch):
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)
        session_key = "__bg__fullclean"
        promote_to_agent_longterm("bg fact", source_session=session_key)

        await cleanup_background_session_artifacts(session_key)

        assert all(
            entry.get("source_session") != session_key
            for entry in load_agent_longterm().get("entries", [])
        )

    @pytest.mark.asyncio
    async def test_sync_cleanup_collaborators_do_not_block_event_loop(
        self,
        state_dir,
        monkeypatch,
    ):
        heartbeat_time: float | None = None

        class SlowSessionManager:
            def destroy(self, *_args, **_kwargs):
                time.sleep(0.08)
                return True

        class SlowMemory:
            state_root = state_dir
            store = SimpleNamespace(evict_session=lambda _key: None)
            activity_log = SimpleNamespace(remove_session=AsyncMock())

            def remove_session_entries(self, _session_key):
                time.sleep(0.08)
                return 0

        async def heartbeat() -> None:
            nonlocal heartbeat_time
            await asyncio.sleep(0.02)
            heartbeat_time = time.perf_counter()

        monkeypatch.setattr(
            "miniagent.assistant.memory.layered_memory.remove_agent_longterm_entries_for_session",
            MagicMock(return_value=0),
        )
        heartbeat_task = asyncio.create_task(heartbeat())
        await cleanup_background_session_artifacts(
            "__bg__nonblocking",
            session_manager=SlowSessionManager(),
            memory=SlowMemory(),
        )
        cleanup_returned_at = time.perf_counter()
        await heartbeat_task

        assert heartbeat_time is not None
        assert heartbeat_time < cleanup_returned_at
