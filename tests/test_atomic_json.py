from __future__ import annotations

import json

import pytest

from miniagent.infrastructure.atomic_json import atomic_dump_json


def test_atomic_dump_json_publishes_complete_document(tmp_path) -> None:
    target = tmp_path / "state.json"

    atomic_dump_json(target, {"items": [1, 2, 3]}, ensure_ascii=False)

    assert json.loads(target.read_text(encoding="utf-8")) == {"items": [1, 2, 3]}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_dump_json_failure_preserves_previous_file(tmp_path) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"generation":1}', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_dump_json(target, {"invalid": object()})

    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 1}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
