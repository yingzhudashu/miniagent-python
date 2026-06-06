"""欢迎界面技能分层统计。"""

from __future__ import annotations

from miniagent.engine.welcome import (
    SkillDisplayCounts,
    compute_skill_display_counts,
    format_skill_display_label,
)
from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.types.skill import Skill, SkillPackage


def _pkg(pkg_id: str, *, scope: str = "global") -> SkillPackage:
    return SkillPackage(
        id=pkg_id,
        name=pkg_id,
        description="test",
        skills=[Skill(id=f"{pkg_id}-tool", name="t", description="d")],
        scope=scope,
    )


def test_global_packages_only_no_session_skills() -> None:
    reg = DefaultSkillRegistry()
    reg.register_package(_pkg("builtin-web"))
    reg.register_package(_pkg("skill-creator"))
    reg.register_package(_pkg("skill-vetter"))

    counts = compute_skill_display_counts(reg, "default")

    assert counts == SkillDisplayCounts(global_packages=3, session_skills=0)
    assert format_skill_display_label(counts) == "3 global skills · 0 session skills"


def test_session_scoped_skills_counted_for_active_session_only() -> None:
    reg = DefaultSkillRegistry()
    reg.register_package(_pkg("builtin-web"))
    reg.register_package(_pkg("sess-a", scope="session:default"))
    reg.register_package(_pkg("sess-b", scope="session:default"))
    reg.register_package(_pkg("other-sess", scope="session:other"))

    counts = compute_skill_display_counts(reg, "default")

    assert counts == SkillDisplayCounts(global_packages=1, session_skills=2)
    assert format_skill_display_label(counts) == "1 global skill · 2 session skills"


def test_session_skills_ignored_without_active_session_id() -> None:
    reg = DefaultSkillRegistry()
    reg.register_package(_pkg("sess-only", scope="session:default"))

    counts = compute_skill_display_counts(reg, None)

    assert counts == SkillDisplayCounts(global_packages=0, session_skills=0)
    assert format_skill_display_label(counts) == "0 global skills · 0 session skills"


def test_instruction_only_global_packages_still_count_as_parent() -> None:
    reg = DefaultSkillRegistry()
    for name in ("skill-creator", "skill-vetter", "github-monitor"):
        reg.register_package(
            SkillPackage(id=name, name=name, description="test", skills=[], scope="global")
        )

    counts = compute_skill_display_counts(reg, "default")

    assert counts == SkillDisplayCounts(global_packages=3, session_skills=0)
    assert format_skill_display_label(counts) == "3 global skills · 0 session skills"
