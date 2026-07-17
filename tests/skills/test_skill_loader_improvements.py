"""loader 改进项测试 — 子技能 ID、纯指令型包 system prompt。"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

from miniagent.agent.types.config import AgentConfig
from miniagent.assistant.skills import loader
from miniagent.assistant.skills.loader import load_skill_package
from miniagent.assistant.skills.registry import DefaultSkillRegistry


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


@pytest.mark.asyncio
async def test_yaml_manifest_entry_fallback_and_invalid_shape(tmp_path) -> None:
    package = tmp_path / "yaml-skill"
    package.mkdir()
    (package / "skill.yaml").write_text(
        "name: yaml-skill\ndescription: yaml desc\nentry_point: custom.md\n",
        encoding="utf-8",
    )
    (package / "custom.md").write_text(
        "---\nkeywords: [one, two]\n---\nUse {baseDir}/data.\n", encoding="utf-8"
    )
    loaded = await load_skill_package(str(package))
    assert loaded is not None and loaded.name == "yaml-skill"
    assert loaded.skills[0].keywords == ["one", "two"]
    assert "yaml-skill/data" in loaded.skills[0].system_prompt.replace("\\", "/")

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "skill.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")
    assert await load_skill_package(str(invalid)) is None


def test_python_skill_entry_points_and_missing_package(tmp_path, monkeypatch) -> None:
    sentinel = object()
    package = tmp_path / "python-skill"
    package.mkdir()
    (package / "index.py").write_text("default = []\n", encoding="utf-8")
    module = SimpleNamespace(default=[sentinel])
    monkeypatch.setattr(loader, "_import_module_from_path", lambda *_args: module)
    assert loader._load_python_skill_definitions(str(package), "python-skill") == [sentinel]
    module.skills = [sentinel]
    assert loader._load_python_skill_definitions(str(package), "python-skill") == [sentinel]
    assert loader._load_python_skill_definitions(str(tmp_path / "missing"), "missing") == []
