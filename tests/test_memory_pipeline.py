"""``memory_pipeline`` — system 侧分层记忆披露测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from miniagent.assistant.infrastructure.paths import resolve_state_dir
from miniagent.assistant.memory.history_archive import diary_file_path
from miniagent.assistant.memory.layered_memory import (
    append_session_day_rollup,
    promote_to_agent_longterm,
)
from miniagent.assistant.memory.memory_pipeline import build_layered_memory_augmentation
from tests.config_helpers import install_test_config


@pytest.fixture(autouse=True)
def isolate_state(tmp_path: Path) -> None:
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


def test_build_layered_memory_disabled(tmp_path: Path) -> None:
    install_test_config(
        tmp_path,
        {
            "paths": {"state_dir": str(tmp_path)},
            "memory": {"layered_inject": False},
        },
    )

    text = build_layered_memory_augmentation("sess", user_input="hi")
    assert text == ""


def test_build_layered_memory_includes_identity(tmp_path: Path) -> None:
    identity = "你是测试 Agent"
    state_root = Path(resolve_state_dir())
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "identity.md").write_text(identity, encoding="utf-8")

    text = build_layered_memory_augmentation("sess", user_input="hello")
    assert text.startswith(identity)


def test_build_layered_memory_diary_preview(tmp_path: Path) -> None:
    session_key = "sess-diary"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = diary_file_path(session_key, day)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    diary_body = "x" * 200
    Path(path).write_text(diary_body, encoding="utf-8")

    text = build_layered_memory_augmentation(session_key, user_input="q")
    assert "本会话今日日记摘录" in text
    assert "…(截断)" in text


def test_build_layered_memory_session_and_agent_lt() -> None:
    append_session_day_rollup(
        "sess-lt",
        day="2026-06-01",
        diary_relative="memory/diary/sess-lt/2026-06-01.md",
        summary="rolled up",
    )
    promote_to_agent_longterm("global fact", source_session="sess-lt")

    text = build_layered_memory_augmentation("sess-lt", user_input="q")
    assert "会话长期记忆 — 日索引" in text
    assert "rolled up" in text
    assert "Agent 长期记忆" in text
    assert "global fact" in text


def test_build_layered_memory_total_truncation(tmp_path: Path) -> None:
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
    for i in range(5):
        promote_to_agent_longterm("f" * 200, source_session=f"sess-{i}")

    text = build_layered_memory_augmentation("sess-big", user_input="q")
    assert "layered_memory 总长度已截断" in text
    assert len(text) <= 120 + len("\n…(layered_memory 总长度已截断)")
