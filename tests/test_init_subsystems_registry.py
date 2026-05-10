"""init_subsystems 完成后主 registry 含联网类内置工具。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miniagent.engine.init import init_subsystems
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.skills.registry import DefaultSkillRegistry


@pytest.mark.asyncio
async def test_init_subsystems_registers_web_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MINI_AGENT_STATE", str(tmp_path))
    monkeypatch.setenv("MINI_AGENT_SKILLS", str(tmp_path / "empty_skills"))
    (tmp_path / "empty_skills").mkdir(parents=True)

    registry = DefaultToolRegistry()
    skill_registry = DefaultSkillRegistry()
    engine = MagicMock()
    SessionManager = MagicMock()
    sm_instance = MagicMock()
    sm_instance.get_or_create = MagicMock()
    SessionManager.return_value = sm_instance

    channel_router = MagicMock()
    channel_router.bind = MagicMock()
    channel_router.set_primary = MagicMock()

    await init_subsystems(
        registry,
        skill_registry,
        engine,
        SessionManager,
        channel_router,
        clawhub=None,
        keyword_index=None,
    )

    names = registry.list()
    assert "web_search" in names
    assert "browser_extract_text" in names
    assert "fetch_url" in names
