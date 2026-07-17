"""Packaging contracts for runtime data required outside a source checkout."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from miniagent.assistant.infrastructure.json_config import (
    JsonConfigLoader,
    _packaged_defaults_path,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_packaged_defaults_match_source_defaults() -> None:
    packaged = json.loads(
        files("miniagent.assistant.resources")
        .joinpath("config.defaults.json")
        .read_text(encoding="utf-8")
    )
    assert "_config_guide" in packaged
    assert "agent" in packaged


def test_loader_can_use_packaged_defaults_without_source_checkout() -> None:
    loader = JsonConfigLoader(
        defaults_path=_packaged_defaults_path(),
        user_path=str(PROJECT_ROOT / "__missing_config_user__.json"),
    )
    assert loader.get("llm.roles.default")
    assert loader.get("agent.max_turns") > 0


def test_baseline_skill_templates_contain_required_runtime_data() -> None:
    templates = files("miniagent.assistant.skills").joinpath("templates")
    required = (
        "builtin-web/SKILL.md",
        "builtin-web/_meta.json",
        "builtin-web/skills/web-tools/SKILL.md",
        "builtin-stackexchange/SKILL.md",
        "builtin-stackexchange/_meta.json",
        "builtin-stackexchange/skills/stackexchange-tools/SKILL.md",
        "skill-creator/SKILL.md",
        "skill-creator/assets/eval_review.html",
        "skill-creator/eval-viewer/viewer.html",
        "skill-creator/references/schemas.md",
        "skill-vetter/SKILL.md",
        "skill-vetter/references/vetting-checklist.md",
    )
    missing = [name for name in required if not templates.joinpath(name).is_file()]
    assert missing == []
