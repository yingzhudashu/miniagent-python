"""get_skills_root 与 ClawHub download 路径一致。"""

from __future__ import annotations

from pathlib import Path

from miniagent.skills.paths import get_skills_root


def test_get_skills_root_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MINIAGENT_PATHS_SKILLS_DIR", str(tmp_path))
    assert get_skills_root() == str(tmp_path)


def test_get_skills_root_default_under_repo(monkeypatch) -> None:
    monkeypatch.delenv("MINIAGENT_PATHS_SKILLS_DIR", raising=False)
    root = Path(__file__).resolve().parent.parent
    expected = root / "workspaces" / "skills"
    assert Path(get_skills_root()) == expected
