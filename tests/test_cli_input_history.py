"""CLI 输入框 ↑↓ 历史：同步预填充、条数上限与会话切换刷新。"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.agent.types.memory import SessionOptions
from miniagent.assistant.engine.cli_history import (
    create_cli_file_history,
    reload_cli_input_history,
    session_user_inputs_for_cli_history,
    sync_preload_buffer_working_lines,
)
from miniagent.assistant.session.manager import DefaultSessionManager


def test_session_user_inputs_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.engine.cli_history.get_config",
        lambda key, default=None: 3 if key == "cli.input_history_max" else default,
    )
    session = MagicMock()
    session.conversation_history = [
        {"role": "user", "content": f"msg-{i}"} for i in range(10)
    ]
    state = {
        "session_manager": MagicMock(get=MagicMock(return_value=session)),
        "active_session_id": "default",
    }
    result = session_user_inputs_for_cli_history(state)
    assert result == ["msg-7", "msg-8", "msg-9"]


def test_sync_preload_buffer_working_lines(tmp_path: Path) -> None:
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.buffer import Buffer

    history_path = tmp_path / "history.txt"
    history_path.write_text("\n# ts\n+alpha\n", encoding="utf-8")
    hist = create_cli_file_history(str(history_path))
    hist.merge_strings_memory_only(["beta", "gamma"])

    buf = Buffer(history=hist)
    assert len(buf._working_lines) == 1

    sync_preload_buffer_working_lines(buf)

    lines = [s.replace("\r", "") for s in buf._working_lines]
    assert lines == ["alpha", "gamma", "beta", ""]
    assert buf.working_index == 3
    assert buf._load_history_task is not None
    assert buf._load_history_task.done()


def test_reload_cli_input_history_merges_session_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.buffer import Buffer

    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    sm = DefaultSessionManager(DefaultToolRegistry())
    session_id = "hist-test"
    session = sm.get_or_create(session_id, SessionOptions(description="test"))
    session.conversation_history.extend(
        [
            {"role": "user", "content": "from-session-a"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "from-session-b"},
        ]
    )

    history_file = str(tmp_path / "cli" / "history.txt")
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    Path(history_file).write_text("\n# ts\n+saved-cmd\n", encoding="utf-8")

    state = {"session_manager": sm, "active_session_id": session_id}
    buf = Buffer(history=create_cli_file_history(history_file))

    reload_cli_input_history(state, buf, history_file)

    lines = [s.replace("\r", "") for s in buf._working_lines]
    assert lines == [
        "saved-cmd",
        "from-session-b",
        "from-session-a",
        "",
    ]
    assert buf.working_index == 3


def test_session_switch_calls_reload_cli_input_history() -> None:
    source = Path(__file__).resolve().parent.parent.joinpath(
        "miniagent", "assistant", "engine", "cli_tui.py"
    ).read_text(encoding="utf-8")
    assert "reload_cli_input_history(state, runtime[\"input_buffer\"], runtime[\"history_file\"])" in source
    switch_block_start = source.index("previous = state")
    switch_block = source[switch_block_start : switch_block_start + 800]
    assert switch_block.count("reload_cli_input_history") >= 1
    assert 'runtime["reset_transcript"]' in switch_block
