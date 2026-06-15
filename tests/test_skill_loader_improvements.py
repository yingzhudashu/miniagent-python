"""loader 改进项测试 — 子技能 ID、纯指令型包 system prompt。"""

from __future__ import annotations

import os
import tempfile

import pytest

from miniagent.skills.loader import load_skill_package
from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.types.config import AgentConfig


@pytest.mark.asyncio
async def test_sub_skill_id_uses_package_name() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "my-pkg")
        sub = os.path.join(pkg_dir, "skills", "web-tools")
        os.makedirs(sub)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: my-pkg\ndescription: d\n---\n")
        with open(os.path.join(sub, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: web-tools\ndescription: web\n---\n")

        pkg = await load_skill_package(pkg_dir)
        assert pkg is not None
        assert len(pkg.skills) == 1
        assert pkg.skills[0].id == "my-pkg-web-tools"


@pytest.mark.asyncio
async def test_sub_skill_ids_unique_across_packages() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        for pkg_name in ("pkg-a", "pkg-b"):
            pkg_dir = os.path.join(tmpdir, pkg_name)
            sub = os.path.join(pkg_dir, "skills", "shared-name")
            os.makedirs(sub)
            with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {pkg_name}\ndescription: d\n---\n")
            with open(os.path.join(sub, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\nname: shared\ndescription: s\n---\nBody\n")

        reg = DefaultSkillRegistry()
        for entry in sorted(os.listdir(tmpdir)):
            pkg = await load_skill_package(os.path.join(tmpdir, entry))
            assert pkg is not None
            reg.register_package(pkg)

        ids = {s.id for s in reg.get_all()}
        assert ids == {"pkg-a-shared-name", "pkg-b-shared-name"}


@pytest.mark.asyncio
async def test_instruction_only_package_injects_system_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "github-monitor")
        os.makedirs(pkg_dir)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: github-monitor\ndescription: monitor repos\n---\n"
                "# GitHub Monitor\n\nTrack repository activity.\n"
            )

        pkg = await load_skill_package(pkg_dir)
        assert pkg is not None
        assert len(pkg.skills) == 1
        skill = pkg.skills[0]
        assert skill.id == "github-monitor"
        assert skill.system_prompt is not None
        assert "GitHub Monitor" in skill.system_prompt

        reg = DefaultSkillRegistry()
        reg.register_package(pkg)
        prompts = reg.get_system_prompts(config=AgentConfig())
        assert any("GitHub Monitor" in p for p in prompts)
