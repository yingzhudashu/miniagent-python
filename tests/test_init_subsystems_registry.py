"""init_subsystems 完成后主 registry 含核心内置工具。

重构说明：web.py 已重命名为 core_tools.py，check_app_availability 已合并到 skills.py。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miniagent.engine.init import init_subsystems
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.skills.registry import DefaultSkillRegistry
from tests.config_helpers import install_test_config


@pytest.mark.asyncio
async def test_init_subsystems_registers_core_tools(tmp_path) -> None:
    """测试 init_subsystems 注册核心工具。"""
    skills_dir = tmp_path / "empty_skills"
    skills_dir.mkdir(parents=True)
    install_test_config(
        tmp_path,
        {
            "paths": {
                "state_dir": str(tmp_path),
                "skills_dir": str(skills_dir),
            },
        },
    )

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
    # 验证核心工具存在
    assert "get_time" in names  # 从 core_tools.py
    assert "check_app_availability" in names  # 从 skills.py
    # 验证基础工具存在
    assert "read_file" in names
    assert "exec_command" in names
    # 验证工具总数 >= 30
    assert len(names) >= 30