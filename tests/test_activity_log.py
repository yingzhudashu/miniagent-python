"""Native asynchronous activity log contract."""

from __future__ import annotations

import pytest

from miniagent.assistant.memory.activity_log import ActivityLogger, _short_json


@pytest.mark.asyncio
async def test_activity_logger_records_full_session(tmp_path) -> None:
    logger = ActivityLogger(str(tmp_path))
    await logger.log_session_start("s1", "question", "cli")
    await logger.log_llm_call("s1", 1, "model", 2, 0, "thinking", {"total_tokens": 3})
    await logger.log_tool_call("s1", "search", "lookup", {}, "result", 5, True)
    await logger.log_final_reply("s1", "answer")
    text = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert all(fragment in text for fragment in ("s1", "question", "model", "search", "answer"))


@pytest.mark.asyncio
async def test_activity_logger_records_incomplete(tmp_path) -> None:
    logger = ActivityLogger(str(tmp_path))
    await logger.log_incomplete("s1", "limit")
    assert "limit" in next(tmp_path.glob("*.md")).read_text(encoding="utf-8")


def test_short_json_is_bounded() -> None:
    assert _short_json({"a": 1}) == '{"a": 1}'
    assert _short_json({"text": "x" * 100}, max_len=20).endswith("...")
