"""Tests for background session artifact cleanup."""

import json
import os

import pytest

from miniagent.engine.bg_session_cleanup import (
    cleanup_background_session_artifacts,
    is_background_session_key,
)
from miniagent.memory.activity_log import ActivityLogger
from miniagent.memory.shared_registry import get_registry, reset_registry
from miniagent.utils.session_id import safe_session_id


class TestIsBackgroundSessionKey:
    def test_bg_prefix(self):
        assert is_background_session_key("__bg__abc123") is True
        assert is_background_session_key("cli-session") is False


class TestCleanupBackgroundSessionArtifacts:
    @pytest.mark.asyncio
    async def test_removes_workspace_memory_and_activity_log(self, state_dir, monkeypatch):
        from miniagent.memory.defaults import reset_process_default_memory_bundle_for_tests

        reset_registry()
        reset_process_default_memory_bundle_for_tests()

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

        registry = get_registry(state_dir)
        registry.register(
            session_key,
            type("E", (), {
                "timestamp": "2026-06-14T00:00:00+00:00",
                "user_snippet": "hello",
                "summary": "test",
                "facts": [],
            })(),
        )

        activity_log = ActivityLogger(base_dir=os.path.join(state_dir, "memory"))
        activity_log.log_session_start(session_key, "background prompt")

        await cleanup_background_session_artifacts(
            session_key,
            activity_log=activity_log,
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
    async def test_execute_task_cleans_disk_after_completion(self, state_dir, monkeypatch):
        import asyncio

        from miniagent.engine.background_tasks import BackgroundTaskManager
        from miniagent.memory.defaults import reset_process_default_memory_bundle_for_tests

        reset_registry()
        reset_process_default_memory_bundle_for_tests()

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
        state = {"skill_toolboxes": [], "skill_prompts": None}

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
