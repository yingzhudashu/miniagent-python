"""Tests for skill registry and loader."""

import os
import tempfile
import pytest
from src.skills.registry import DefaultSkillRegistry
from src.skills.loader import discover_skill_packages
from src.types.skill import Skill


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
