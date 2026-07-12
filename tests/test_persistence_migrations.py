"""版本化状态迁移的内存、备份和失败原子性测试。"""

from __future__ import annotations

import json

import pytest

from miniagent.infrastructure import state_schemas
from miniagent.infrastructure.persistence import (
    StateMigrationError,
    StateSchema,
    dump_state_file,
    get_state_schema,
    load_state_file,
    migrate_state,
    migrate_state_file,
    register_state_schema,
)
from miniagent.infrastructure.state_schemas import install_builtin_state_schemas


def test_session_legacy_migrates_without_mutating_input() -> None:
    install_builtin_state_schemas()
    legacy = {"session_id": "s1", "title": "旧会话"}
    migrated = migrate_state("session_config", legacy)
    assert migrated == {"session_id": "s1", "title": "旧会话", "schema_version": 1}
    assert "schema_version" not in legacy


def test_scheduled_legacy_version_is_normalized() -> None:
    migrated = migrate_state("scheduled_tasks", {"version": 2, "tasks": []})
    assert migrated == {"version": 2, "tasks": [], "schema_version": 2}


def test_builtin_migration_steps_normalize_payloads() -> None:
    payload: dict[str, object] = {}
    assert state_schemas._identity(payload) is payload
    assert state_schemas._scheduled_v0_to_v1(payload) == {"tasks": []}
    assert state_schemas._scheduled_v1_to_v2({"version": 1, "tasks": []}) == {"tasks": []}


def test_builtin_schema_install_reraises_unexpected_registry_error(monkeypatch) -> None:
    monkeypatch.setattr(
        state_schemas,
        "register_state_schema",
        lambda _schema: (_ for _ in ()).throw(ValueError("invalid registry")),
    )
    with pytest.raises(ValueError, match="invalid registry"):
        state_schemas.install_builtin_state_schemas()


def test_explicit_file_migration_creates_backup(tmp_path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"session_id":"s1"}', encoding="utf-8")
    backup = migrate_state_file("session_config", target)
    assert backup is not None
    assert json.loads(backup.read_text(encoding="utf-8")) == {"session_id": "s1"}
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1


def test_future_version_is_rejected_without_writing(tmp_path) -> None:
    target = tmp_path / "config.json"
    original = '{"schema_version":99,"session_id":"s1"}'
    target.write_text(original, encoding="utf-8")
    with pytest.raises(StateMigrationError, match="高于程序支持"):
        migrate_state_file("session_config", target)
    assert target.read_text(encoding="utf-8") == original
    assert not (tmp_path / "config.json.bak").exists()


def test_current_file_is_not_rewritten_or_backed_up(tmp_path) -> None:
    target = tmp_path / "config.json"
    target.write_text('{"schema_version":1,"session_id":"s1"}', encoding="utf-8")
    assert migrate_state_file("session_config", target) is None
    assert not (tmp_path / "config.json.bak").exists()


def test_load_and_dump_state_file_share_schema_contract(tmp_path) -> None:
    target = tmp_path / "dream.json"
    dump_state_file("dream_state", target, {"per_session": {}})
    assert load_state_file("dream_state", target) == {
        "per_session": {},
        "schema_version": 1,
    }
    assert not (tmp_path / "dream.json.bak").exists()


def test_legacy_list_schema_is_wrapped_and_backed_up(tmp_path) -> None:
    target = tmp_path / "history.json"
    target.write_text('[{"id":"p1"}]', encoding="utf-8")
    loaded = load_state_file("self_opt_proposal_index", target)
    assert loaded == {"entries": [{"id": "p1"}], "schema_version": 1}
    assert json.loads((tmp_path / "history.json.bak").read_text(encoding="utf-8")) == [{"id": "p1"}]


def test_legacy_session_history_is_wrapped_and_backed_up(tmp_path) -> None:
    """旧会话历史数组迁移为带版本的文档，同时保留原数组备份。"""
    target = tmp_path / "history.json"
    legacy = [{"role": "user", "content": "你好"}]
    target.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    loaded = load_state_file("session_history", target)

    assert loaded == {"messages": legacy, "schema_version": 1}
    assert json.loads((tmp_path / "history.json.bak").read_text(encoding="utf-8")) == legacy


@pytest.mark.parametrize("version", [True, "1", -1])
def test_invalid_schema_version_is_rejected(version) -> None:
    with pytest.raises(StateMigrationError, match="schema_version"):
        migrate_state("session_config", {"schema_version": version})


def test_unknown_schema_and_non_object_file_are_rejected(tmp_path) -> None:
    with pytest.raises(StateMigrationError, match="未知状态 schema"):
        get_state_schema("missing")
    target = tmp_path / "array.json"
    target.write_text("[]", encoding="utf-8")
    with pytest.raises(StateMigrationError, match="顶层必须"):
        migrate_state_file("session_config", target)


def test_registry_rejects_invalid_and_duplicate_schemas() -> None:
    with pytest.raises(ValueError, match="大于等于 1"):
        register_state_schema(StateSchema("invalid-version", 0, {}))
    with pytest.raises(ValueError, match="已注册"):
        register_state_schema(StateSchema("session_config", 1, {}))


def test_missing_migration_step_is_reported() -> None:
    register_state_schema(StateSchema("test-missing-step", 2, {}))
    with pytest.raises(StateMigrationError, match="缺少 0 → 1"):
        migrate_state("test-missing-step", {})
