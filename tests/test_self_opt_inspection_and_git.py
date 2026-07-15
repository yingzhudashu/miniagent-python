"""自优化 Git 快照与项目检查器的本地契约测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from miniagent.assistant.self_opt import git_snapshot
from miniagent.assistant.self_opt.inspector import (
    _analyze_module,
    _count_lines,
    _count_python_files,
    _estimate_test_coverage,
    _identify_pain_points,
    inspect_project,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "MiniAgent Tests")
    (repo / "tracked.txt").write_text("base", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")
    return repo


@pytest.mark.asyncio
async def test_git_snapshot_sync_and_async_roundtrip(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert git_snapshot.is_in_git_repo(str(repo))
    assert await git_snapshot.is_in_git_repo_async(str(repo))
    assert not git_snapshot.has_uncommitted_changes(str(repo))

    (repo / "tracked.txt").write_text("changed", encoding="utf-8")
    assert git_snapshot.has_uncommitted_changes(str(repo))
    assert await git_snapshot.has_uncommitted_changes_async(str(repo))
    snapshot = git_snapshot.create_snapshot("sync", path=str(repo))
    assert snapshot["success"] and snapshot["ref"]
    restored = git_snapshot.rollback_snapshot(snapshot["ref"], path=str(repo))
    assert restored["success"]

    (repo / "tracked.txt").write_text("changed-again", encoding="utf-8")
    snapshot_async = await git_snapshot.create_snapshot_async("async", path=str(repo))
    assert snapshot_async["success"]
    restored_async = await git_snapshot.rollback_snapshot_async(
        snapshot_async["ref"], path=str(repo)
    )
    assert restored_async["success"]


@pytest.mark.asyncio
async def test_git_snapshot_non_repository_degrades(tmp_path: Path) -> None:
    path = str(tmp_path)
    assert not git_snapshot.is_in_git_repo(path)
    assert not await git_snapshot.is_in_git_repo_async(path)
    assert not git_snapshot.has_uncommitted_changes(path)
    assert not await git_snapshot.has_uncommitted_changes_async(path)
    assert not git_snapshot.create_snapshot("x", path=path)["success"]
    assert not (await git_snapshot.create_snapshot_async("x", path=path))["success"]
    assert not git_snapshot.rollback_snapshot("stash@{0}", path=path)["success"]
    assert not (await git_snapshot.rollback_snapshot_async("stash@{0}", path=path))["success"]


@pytest.mark.asyncio
async def test_project_inspector_covers_metrics_modules_and_pain_points(tmp_path: Path) -> None:
    package = tmp_path / "pkg_without_init"
    package.mkdir()
    source = package / "large.py"
    source.write_text(
        "def f(x):\n" + "    if x:\n        return x\n" * 8 + "# TODO\n" + "x=1\n" * 15_000,
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text("def test_x(): pass\n", encoding="utf-8")

    assert _count_python_files(str(tmp_path)) == 2
    assert _count_lines(str(tmp_path)) > 100
    assert _estimate_test_coverage(str(tmp_path)) > 0
    analysis = _analyze_module(str(source))
    assert analysis.lines > 500
    assert analysis.issues
    pains = _identify_pain_points(str(tmp_path))
    assert {pain.category for pain in pains} >= {"architecture", "maintainability", "documentation"}

    report = await inspect_project(str(tmp_path))
    assert report.metrics
    assert report.modules
    assert report.pain_points
    assert "发现" in report.summary

