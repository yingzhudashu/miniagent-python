"""Performance audit path discovery handles normal dirty-worktree deletions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_audit_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "performance_audit.py"
    spec = importlib.util.spec_from_file_location("performance_audit_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracked_paths_skip_index_entries_deleted_from_worktree(tmp_path, monkeypatch) -> None:
    module = _load_audit_module()
    present = tmp_path / "miniagent" / "present.py"
    present.parent.mkdir()
    present.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=b"miniagent/deleted.py\0miniagent/present.py\0"
        ),
    )
    assert module._tracked_paths() == [Path("miniagent/present.py")]
