"""get_skills_root 与 ClawHub download 路径一致。"""

from __future__ import annotations

from pathlib import Path

from miniagent.skills.paths import get_skills_root
from tests.config_helpers import install_test_config


def test_get_skills_root_config_override(tmp_path) -> None:
    install_test_config(tmp_path, {"paths": {"skills_dir": str(tmp_path)}})
    assert get_skills_root() == str(tmp_path)


def test_get_skills_root_default_under_repo(tmp_path) -> None:
    install_test_config(tmp_path, {})
    root = Path(__file__).resolve().parent.parent
    expected = root / "workspaces" / "skills"
    assert Path(get_skills_root()) == expected
