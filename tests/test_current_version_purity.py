from pathlib import Path

from scripts.check_current_version import check_current_version


def test_repository_contains_only_current_runtime_designs() -> None:
    root = Path(__file__).resolve().parents[1]
    assert check_current_version(root / "miniagent") == []
