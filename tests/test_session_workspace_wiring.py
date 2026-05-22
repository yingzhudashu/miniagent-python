"""会话 files 目录与 AgentConfig.session_workspace 接线。"""

from __future__ import annotations

import os

import pytest


def test_get_session_files_path_after_get_or_create(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.session.manager import DefaultSessionManager

    sm = DefaultSessionManager(DefaultToolRegistry())
    sm.get_or_create("unit-test-session", None)
    fp = sm.get_session_files_path("unit-test-session")
    assert fp
    assert fp.replace("\\", "/").endswith("/files")
    assert os.path.isdir(fp)


def test_build_execution_system_prompt_includes_files_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = str(tmp_path / "files")
    os.makedirs(root, exist_ok=True)
    from miniagent.core.executor import build_execution_system_prompt

    s = build_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt=None,
        plan_summary="T",
        keyword_context=None,
        session_files_root=root,
    )
    assert "默认文件根目录" in s
    assert "read_file" in s
    assert os.path.abspath(root) in s
