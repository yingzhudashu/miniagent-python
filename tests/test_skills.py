"""Tests for skill registry and loader."""

import os
from pathlib import Path
import tempfile
import pytest
from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.skills.clawhub_client import skill_install_dir_name
from miniagent.skills.loader import discover_skill_packages, parse_skill_md
from miniagent.types.skill import Skill


def test_skill_install_dir_name_flattens_nested_slug():
    assert skill_install_dir_name("skill-creator") == "skill-creator"
    assert skill_install_dir_name("org/skill-creator") == "skill-creator"
    assert skill_install_dir_name(r"org\pkg-name") == "pkg-name"


class TestDefaultSkillRegistry:
    def test_register_skill(self):
        reg = DefaultSkillRegistry()
        skill = Skill(id="test-skill", name="Test Skill", description="A test")
        reg.register(skill)
        skills = reg.get_all()
        assert any(s.id == "test-skill" for s in skills)

    def test_register_duplicate_overwrites(self):
        reg = DefaultSkillRegistry()
        skill1 = Skill(id="dup", name="First", description="First")
        skill2 = Skill(id="dup", name="Second", description="Second")
        reg.register(skill1)
        reg.register(skill2)  # Should overwrite without error
        found = reg.get("dup")
        assert found is not None
        assert found.name == "Second"

    def test_get_skill_by_id(self):
        reg = DefaultSkillRegistry()
        skill = Skill(id="find-me", name="Find Me", description="Find")
        reg.register(skill)
        found = reg.get("find-me")
        assert found is not None
        assert found.name == "Find Me"

    def test_get_nonexistent(self):
        reg = DefaultSkillRegistry()
        assert reg.get("ghost") is None


@pytest.mark.asyncio
class TestSkillLoader:
    async def test_discover_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            packages = await discover_skill_packages(tmpdir)
            assert packages == []

    async def test_discover_with_skill_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = os.path.join(tmpdir, "test-skill")
            os.makedirs(skill_dir)
            # Write a minimal skill.py
            with open(os.path.join(skill_dir, "skill.py"), "w") as f:
                f.write("name = 'Test Skill'\ndescription = 'A test skill'\n")
            packages = await discover_skill_packages(tmpdir)
            assert isinstance(packages, list)

    async def test_repo_builtin_skill_packages_load(self):
        repo_root = Path(__file__).resolve().parent.parent
        skills_root = repo_root / "workspaces" / "skills"
        packages = await discover_skill_packages(str(skills_root))
        ids = sorted(p.id for p in packages)
        assert "skill-creator" in ids
        assert "skill-vetter" in ids
        creator = next(p for p in packages if p.id == "skill-creator")
        assert creator.skill_md
        cmeta, _ = parse_skill_md(creator.skill_md)
        assert cmeta.get("name") == "skill-creator"
        vetter = next(p for p in packages if p.id == "skill-vetter")
        assert vetter.skill_md
        vmeta, _ = parse_skill_md(vetter.skill_md)
        assert vmeta.get("name") == "skill-vetter"
