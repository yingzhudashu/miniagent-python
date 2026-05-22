"""install_skill triggers refresh_skills when cli_loop_state is present."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.tools import skills as skills_module
from miniagent.types.tool import ToolContext


@pytest.mark.asyncio
async def test_install_skill_hot_loads_without_restart() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "hot-skill")

        registry = DefaultToolRegistry()
        skill_registry = DefaultSkillRegistry()
        rt = MagicMock()
        rt.registry = registry
        rt.skill_registry = skill_registry
        state: dict = {"runtime_ctx": rt, "session_manager": None}

        async def _fake_download(
            slug: str,
            version: str | None = None,
            *,
            skills_root: str | None = None,
        ) -> dict:
            os.makedirs(os.path.join(pkg_dir, "skills", "main"), exist_ok=True)
            with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: hot-skill\ndescription: hot\n---\n")
            with open(
                os.path.join(pkg_dir, "skills", "main", "tools.py"), "w", encoding="utf-8"
            ) as f:
                f.write(
                    "from miniagent.types.tool import ToolDefinition\n"
                    "hot_tool = ToolDefinition(\n"
                    "    schema={'type': 'function', 'function': {'name': 'hot_tool', "
                    "'description': 'd', 'parameters': {'type': 'object'}}},\n"
                    "    handler=lambda a, c: None,\n"
                    "    permission='sandbox',\n"
                    "    help_text='hot',\n"
                    ")\n"
                )
            return {"path": pkg_dir, "files": ["SKILL.md"]}

        fake = MagicMock()
        fake.download = AsyncMock(side_effect=_fake_download)
        fake.get_detail = AsyncMock(return_value={"version": "1.0.0"})

        ctx = ToolContext(
            cwd=".",
            allowed_paths=["."],
            permission="allowlist",
            clawhub=fake,
            cli_loop_state=state,
        )

        with patch.object(skills_module, "_get_skills_root", return_value=tmpdir):
            result = await skills_module._install_handler(
                {"slug": "hot-skill"},
                ctx,
            )

        assert result.success is True
        assert "热加载" in result.content
        assert registry.get("hot_tool") is not None


@pytest.mark.asyncio
async def test_install_skill_nested_slug_uses_flat_dir() -> None:
    """author/pkg slug 的安装预检路径应对齐 skill_install_dir_name。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_root = os.path.join(tmpdir, "skills")
        os.makedirs(skills_root)
        flat_dir = os.path.join(skills_root, "pkg-only")
        os.makedirs(flat_dir)

        registry = DefaultToolRegistry()
        skill_registry = DefaultSkillRegistry()
        rt = MagicMock()
        rt.registry = registry
        rt.skill_registry = skill_registry
        state: dict = {"runtime_ctx": rt}

        fake = MagicMock()
        fake.download = AsyncMock(return_value={"path": flat_dir, "files": []})
        fake.get_detail = AsyncMock(return_value={"version": "1"})

        ctx = ToolContext(
            cwd=".",
            allowed_paths=["."],
            permission="allowlist",
            clawhub=fake,
            cli_loop_state=state,
        )

        with patch.object(skills_module, "_get_skills_root", return_value=skills_root):
            result = await skills_module._install_handler(
                {"slug": "org/pkg-only"},
                ctx,
            )

        assert result.success is False
        assert "pkg-only" in result.content
        fake.download.assert_not_awaited()
