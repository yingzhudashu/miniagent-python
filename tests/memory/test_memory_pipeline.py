"""System-prompt disclosure tests for layered memory."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from miniagent.assistant.infrastructure.paths import resolve_state_dir
from miniagent.assistant.memory.history_archive import diary_file_path
from miniagent.assistant.memory.layered_memory import LongTermMemoryStore
from miniagent.assistant.memory.memory_pipeline import build_layered_memory_augmentation
from miniagent.assistant.state import StateStore
from tests.support.config import install_test_config


@pytest_asyncio.fixture
async def longterm(tmp_path: Path):
    install_test_config(
        tmp_path,
        {
            "paths": {"state_dir": str(tmp_path)},
            "memory": {
                "layered_inject": True,
                "layered_max_chars": 500,
                "diary_preview_chars": 50,
                "layered_session_lt": True,
                "layered_agent_lt": True,
            },
        },
    )
    async with StateStore(tmp_path) as state_store:
        yield LongTermMemoryStore(state_store)


@pytest.mark.asyncio
async def test_build_layered_memory_disabled(tmp_path: Path, longterm) -> None:
    install_test_config(
        tmp_path,
        {"paths": {"state_dir": str(tmp_path)}, "memory": {"layered_inject": False}},
    )
    assert await build_layered_memory_augmentation(
        "sess", user_input="hi", longterm=longterm
    ) == ""


@pytest.mark.asyncio
async def test_build_layered_memory_includes_identity(tmp_path: Path, longterm) -> None:
    identity = "你是测试 Agent"
    state_root = Path(resolve_state_dir())
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "identity.md").write_text(identity, encoding="utf-8")

    text = await build_layered_memory_augmentation(
        "sess", user_input="hello", longterm=longterm
    )
    assert text.startswith(identity)


@pytest.mark.asyncio
async def test_build_layered_memory_diary_preview(longterm) -> None:
    session_key = "sess-diary"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(diary_file_path(session_key, day))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * 200, encoding="utf-8")

    text = await build_layered_memory_augmentation(
        session_key, user_input="q", longterm=longterm
    )
    assert "本会话今日日记摘录" in text
    assert "…(截断)" in text


@pytest.mark.asyncio
async def test_build_layered_memory_session_and_agent_profiles(longterm) -> None:
    await longterm.append_session_day_rollup(
        "sess-lt",
        day="2026-06-01",
        diary_relative="memory/diary/sess-lt/2026-06-01.md",
        summary="rolled up",
    )
    await longterm.promote("global fact", source_session="sess-lt")

    text = await build_layered_memory_augmentation(
        "sess-lt", user_input="q", longterm=longterm
    )
    assert "会话长期记忆 — 日索引" in text
    assert "rolled up" in text
    assert "Agent 长期记忆" in text
    assert "global fact" in text


@pytest.mark.asyncio
async def test_build_layered_memory_total_truncation(
    tmp_path: Path,
    longterm,
) -> None:
    install_test_config(
        tmp_path,
        {
            "paths": {"state_dir": str(tmp_path)},
            "memory": {
                "layered_inject": True,
                "layered_max_chars": 120,
                "layered_session_lt": True,
                "layered_agent_lt": True,
            },
        },
    )
    for index in range(5):
        await longterm.promote("f" * 200, source_session=f"session-{index}")

    text = await build_layered_memory_augmentation(
        "sess-big", user_input="q", longterm=longterm
    )
    assert "layered_memory 总长度已截断" in text
    assert len(text) <= 120 + len("\n…(layered_memory 总长度已截断)")
