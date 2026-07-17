"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_uninstall_skill_hot_refreshes_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.assistant.skills.refresh as refresh_module
    skill_tools = importlib.import_module("miniagent.assistant.tools.skills")

    (tmp_path / "demo").mkdir()
    monkeypatch.setattr(skill_tools, "_get_skills_root", lambda: str(tmp_path))

    async def refreshed(*_args, **_kwargs):
        return SimpleNamespace(removed_tools=["tool"])

    monkeypatch.setattr(refresh_module, "refresh_skills", refreshed)
    runtime = SimpleNamespace(registry=object(), skill_registry=object())
    ctx = SimpleNamespace(
        cli_loop_state={"runtime_ctx": runtime, "session_manager": None}
    )
    result = await skill_tools._uninstall_handler({"slug": "demo"}, ctx)
    assert result.success
    assert "已从当前 Agent 中移除" in result.content
