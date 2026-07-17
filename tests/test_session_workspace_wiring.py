"""会话 files 目录与 AgentConfig.session_workspace 接线。"""

from __future__ import annotations

import os

import pytest


def test_get_session_files_path_after_get_or_create(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", str(tmp_path))
    from miniagent.agent.tools.registry import DefaultToolRegistry
    from miniagent.assistant.session.manager import DefaultSessionManager

    sm = DefaultSessionManager(DefaultToolRegistry())
    sm.get_or_create("unit-test-session", None)
    fp = sm.get_session_files_path("unit-test-session")
    assert fp
    assert fp.replace("\\", "/").endswith("/files")
    assert os.path.isdir(fp)


def test_build_current_turn_context_includes_files_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = str(tmp_path / "files")
    os.makedirs(root, exist_ok=True)
    from miniagent.agent.executor import build_current_turn_user_context

    s = build_current_turn_user_context(
        user_input="task",
        plan_summary="T",
        keyword_context=None,
        session_files_root=root,
    )
    assert "默认文件根目录" in s
    assert "工具路径参数" in s
    assert os.path.abspath(root) in s
