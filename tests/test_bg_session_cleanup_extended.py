"""Extended cleanup tests: multi-day activity log, trace shards, agent_lt."""

from __future__ import annotations

import json
import os

import pytest

from miniagent.engine.bg_session_cleanup import cleanup_background_session_artifacts
from miniagent.infrastructure.trace_stats import remove_session_from_trace_files
from miniagent.memory.activity_log import ActivityLogger
from miniagent.memory.layered_memory import (
    load_agent_longterm,
    promote_to_agent_longterm,
    remove_agent_longterm_entries_for_session,
)


class TestActivityLogMultiDayCleanup:
    def test_remove_session_from_all_md_files(self, state_dir):
        session_key = "__bg__multiday"
        base = os.path.join(state_dir, "memory")
        os.makedirs(base, exist_ok=True)

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(base, f"{day}.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"---\n## {session_key} (cli)\n\n### 用户输入\n\nhello\n\n")

        logger = ActivityLogger(base_dir=base)
        logger.remove_session(session_key)

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(base, f"{day}.md")
            assert not os.path.exists(path) or session_key not in open(path, encoding="utf-8").read()


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
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

        removed = remove_session_from_trace_files(session_key)
        assert removed == 2

        for day in ("2026-06-13", "2026-06-14"):
            path = os.path.join(trace_dir, f"trace-{day}.jsonl")
            with open(path, encoding="utf-8") as f:
                content = f.read()
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

        removed = remove_session_from_trace_files("remove")

        assert removed == 1
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        assert lines == [
            "{malformed}",
            json.dumps({"session_key": "keep", "type": "y"}),
        ]


class TestAgentLtCleanup:
    def test_remove_agent_longterm_entries_for_session(self, state_dir, monkeypatch):
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)

        promote_to_agent_longterm("keep me", source_session="cli-main")
        promote_to_agent_longterm("remove me", source_session="__bg__agentlt")

        removed = remove_agent_longterm_entries_for_session("__bg__agentlt")
        assert removed == 1

        doc = load_agent_longterm()
        texts = [e.get("text") for e in doc.get("entries", [])]
        assert "remove me" not in texts
        assert "keep me" in texts


class TestCleanupIntegrationAgentLt:
    @pytest.mark.asyncio
    async def test_cleanup_removes_agent_lt_entries(self, state_dir, monkeypatch):
        monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", state_dir)
        session_key = "__bg__fullclean"
        promote_to_agent_longterm("bg fact", source_session=session_key)

        await cleanup_background_session_artifacts(session_key)

        doc = load_agent_longterm()
        assert all(
            e.get("source_session") != session_key for e in doc.get("entries", [])
        )
