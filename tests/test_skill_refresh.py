"""Tests for runtime skill refresh."""

from __future__ import annotations

import os
import tempfile

import pytest

from miniagent.agent.types.skill import Skill
from miniagent.agent.types.tool import ToolDefinition
from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
from miniagent.assistant.skills.refresh import refresh_skills
from miniagent.assistant.skills.registry import DefaultSkillRegistry


def _minimal_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        schema={
            "type": "function",
            "function": {"name": name, "description": "t", "parameters": {"type": "object"}},
        },
        handler=lambda _a, _c: None,  # type: ignore[assignment]
        permission="sandbox",
        help_text="test",
    )


@pytest.mark.asyncio
async def test_refresh_skills_full_loads_tools() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "demo-pkg")
        os.makedirs(os.path.join(pkg_dir, "skills", "sub1"))
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: demo-pkg\ndescription: demo\n---\n")
        tools_py = os.path.join(pkg_dir, "skills", "sub1", "tools.py")
        with open(tools_py, "w", encoding="utf-8") as f:
            f.write(
                "from miniagent.agent.types.tool import ToolDefinition\n"
                "demo_tool = ToolDefinition(\n"
                "    schema={'type': 'function', 'function': {'name': 'demo_tool', "
                "'description': 'd', 'parameters': {'type': 'object'}}},\n"
                "    handler=lambda a, c: None,\n"
                "    permission='sandbox',\n"
                "    help_text='demo',\n"
                ")\n"
            )

        registry = DefaultToolRegistry()
        skill_registry = DefaultSkillRegistry()
        result = await refresh_skills(
            registry,
            skill_registry,
            skills_root=tmpdir,
        )
        assert "demo-pkg" in result.package_ids
        assert registry.get("demo_tool") is not None
        assert len(result.loaded_skills) >= 1


@pytest.mark.asyncio
async def test_refresh_skills_incremental_package() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "incr-pkg")
        os.makedirs(pkg_dir)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: incr\ndescription: incr\n---\n")

        registry = DefaultToolRegistry()
        skill_registry = DefaultSkillRegistry()
        await refresh_skills(registry, skill_registry, skills_root=tmpdir)
        assert skill_registry.get_package("incr-pkg") is not None

        sub = os.path.join(pkg_dir, "skills", "t1")
        os.makedirs(sub)
        with open(os.path.join(sub, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: t1\ndescription: t\n---\n")
        with open(os.path.join(sub, "tools.py"), "w", encoding="utf-8") as f:
            f.write(
                "from miniagent.agent.types.tool import ToolDefinition\n"
                "incr_tool = ToolDefinition(\n"
                "    schema={'type': 'function', 'function': {'name': 'incr_tool', "
                "'description': 'd', 'parameters': {'type': 'object'}}},\n"
                "    handler=lambda a, c: None,\n"
                "    permission='sandbox',\n"
                "    help_text='demo',\n"
                ")\n"
            )

        fr = await refresh_skills(
            registry,
            skill_registry,
            package_dir=pkg_dir,
        )
        assert registry.get("incr_tool") is not None
        assert "incr_tool" in fr.added_tools or registry.get("incr_tool")


@pytest.mark.asyncio
async def test_unregister_package_removes_tools() -> None:
    reg = DefaultSkillRegistry()
    skill = Skill(
        id="pkg-a-s1",
        name="S",
        description="d",
        tools={"t1": _minimal_tool("t1")},
    )
    from miniagent.agent.types.skill import SkillPackage

    pkg = SkillPackage(id="pkg-a", name="A", description="a", skills=[skill])
    reg.register_package(pkg)
    _, tool_names = reg.unregister_package("pkg-a")
    assert reg.get("pkg-a-s1") is None
    assert "t1" in tool_names


def test_clear_packages_collects_gated_skill_tool_names() -> None:
    """全量 refresh 卸载工具名须包含被 gating 的技能，避免幽灵工具。"""
    from miniagent.agent.types.skill import SkillMetadata

    reg = DefaultSkillRegistry()
    reg.register(
        Skill(
            id="gated-pkg-s1",
            name="G",
            description="d",
            tools={"ghost_tool": _minimal_tool("ghost_tool")},
            metadata=SkillMetadata(env=["NONEXISTENT_ENV_FOR_GHOST_XYZ"]),
        )
    )
    assert "ghost_tool" not in reg.get_all_tools()
    _, tool_names = reg.clear_packages()
    assert "ghost_tool" in tool_names
    assert reg.get("gated-pkg-s1") is None


@pytest.mark.asyncio
async def test_refresh_full_rescan_removes_tools_when_package_gone() -> None:
    """全量重扫空目录时，应卸载此前技能注册的工具（含曾写入主 registry 的）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "gone-pkg")
        sub = os.path.join(pkg_dir, "skills", "sub")
        os.makedirs(sub)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: gone-pkg\ndescription: d\n---\n")
        with open(os.path.join(sub, "tools.py"), "w", encoding="utf-8") as f:
            f.write(
                "from miniagent.agent.types.tool import ToolDefinition\n"
                "orphan_tool = ToolDefinition(\n"
                "    schema={'type': 'function', 'function': {'name': 'orphan_tool', "
                "'description': 'd', 'parameters': {'type': 'object'}}},\n"
                "    handler=lambda a, c: None,\n"
                "    permission='sandbox',\n"
                "    help_text='orphan',\n"
                ")\n"
            )
        with open(os.path.join(sub, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: sub\ndescription: d\n---\n")

        registry = DefaultToolRegistry()
        skill_registry = DefaultSkillRegistry()
        await refresh_skills(registry, skill_registry, skills_root=tmpdir)
        assert registry.get("orphan_tool") is not None

        import shutil

        shutil.rmtree(pkg_dir)
        await refresh_skills(registry, skill_registry, skills_root=tmpdir)
        assert registry.get("orphan_tool") is None


@pytest.mark.asyncio
async def test_refresh_builtin_wins_on_tool_name_clash() -> None:
    from miniagent.assistant.engine.builtin_tools import register_builtin_tools

    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "clash-pkg")
        sub = os.path.join(pkg_dir, "skills", "sub")
        os.makedirs(sub)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: clash\ndescription: d\n---\n")
        with open(os.path.join(sub, "tools.py"), "w", encoding="utf-8") as f:
            f.write(
                "from miniagent.agent.types.tool import ToolDefinition\n"
                "read_file = ToolDefinition(\n"
                "    schema={'type': 'function', 'function': {'name': 'read_file', "
                "'description': 'skill', 'parameters': {'type': 'object'}}},\n"
                "    handler=lambda a, c: None,\n"
                "    permission='sandbox',\n"
                "    help_text='from-skill',\n"
                ")\n"
            )
        with open(os.path.join(sub, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: sub\ndescription: d\n---\n")

        registry = DefaultToolRegistry()
        register_builtin_tools(registry)
        builtin = registry.get("read_file")
        assert builtin is not None
        assert builtin.help_text != "from-skill"

        skill_registry = DefaultSkillRegistry()
        await refresh_skills(registry, skill_registry, skills_root=tmpdir)
        after = registry.get("read_file")
        assert after is not None
        assert after.help_text != "from-skill"


@pytest.mark.asyncio
async def test_refresh_reloads_tools_py_after_edit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "hot-pkg")
        sub = os.path.join(pkg_dir, "skills", "sub")
        os.makedirs(sub)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: hot-pkg\ndescription: d\n---\n")
        tools_py = os.path.join(sub, "tools.py")
        with open(tools_py, "w", encoding="utf-8") as f:
            f.write(
                "MARKER = 'v1'\n"
                "from miniagent.agent.types.tool import ToolDefinition\n"
                "def _h(a, c):\n"
                "    return None\n"
                "hot_tool = ToolDefinition(\n"
                "    schema={'type': 'function', 'function': {'name': 'hot_tool', "
                "'description': 'd', 'parameters': {'type': 'object'}}},\n"
                "    handler=_h,\n"
                "    permission='sandbox',\n"
                "    help_text='v1',\n"
                ")\n"
            )
        with open(os.path.join(sub, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: sub\ndescription: d\n---\n")

        registry = DefaultToolRegistry()
        skill_registry = DefaultSkillRegistry()
        await refresh_skills(registry, skill_registry, skills_root=tmpdir)
        t1 = registry.get("hot_tool")
        assert t1 is not None
        assert t1.help_text == "v1"

        with open(tools_py, "w", encoding="utf-8") as f:
            f.write(
                "MARKER = 'v2'\n"
                "from miniagent.agent.types.tool import ToolDefinition\n"
                "def _h(a, c):\n"
                "    return None\n"
                "hot_tool = ToolDefinition(\n"
                "    schema={'type': 'function', 'function': {'name': 'hot_tool', "
                "'description': 'd', 'parameters': {'type': 'object'}}},\n"
                "    handler=_h,\n"
                "    permission='sandbox',\n"
                "    help_text='v2',\n"
                ")\n"
            )
        import time

        time.sleep(1.1)
        os.utime(tools_py, None)

        await refresh_skills(registry, skill_registry, package_dir=pkg_dir)
        t2 = registry.get("hot_tool")
        assert t2 is not None
        assert t2.help_text == "v2"


@pytest.mark.asyncio
async def test_registry_gating_filters_toolboxes() -> None:
    reg = DefaultSkillRegistry()
    from miniagent.agent.types.skill import SkillMetadata

    reg.register(
        Skill(
            id="gated",
            name="G",
            description="d",
            metadata=SkillMetadata(env=["NONEXISTENT_ENV_XYZ_12345"]),
            toolboxes=[],
        )
    )
    reg.register(Skill(id="open", name="O", description="d", toolboxes=[]))
    eligible = reg.get_eligible_skills()
    ids = {s.id for s in eligible}
    assert "open" in ids
    assert "gated" not in ids
    assert len(reg.get_system_prompts()) == len([s for s in eligible if s.system_prompt])
