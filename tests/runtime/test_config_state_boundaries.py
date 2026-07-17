"""Focused regressions migrated from test_core_helper_edge_matrix.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.assistant.engine.commands.instance_commands import handle_instance
from miniagent.assistant.engine.commands.markdown import escape_markdown_cell
from miniagent.assistant.engine.commands.runtime_commands import _capture_call, _respond
from miniagent.assistant.infrastructure.json_config import (
    _compatible_config_type,
    _validate_user_keys,
)
from miniagent.assistant.infrastructure.persistence import (
    StateSchema,
    StateSchemaError,
    load_state_file,
)


def test_config_type_and_object_conflict_validation() -> None:
    assert _compatible_config_type(True, False)
    assert not _compatible_config_type(True, 1)
    assert _compatible_config_type(1.0, 1)
    assert not _compatible_config_type(1.0, True)
    assert _compatible_config_type(1, 2)
    assert not _compatible_config_type(1, False)
    assert _compatible_config_type("x", "y")
    with pytest.raises(ValueError, match="nested 应为 object"):
        _validate_user_keys({"nested": {"enabled": True}}, {"nested": "bad"})

def test_state_schema_rejects_invalid_current_shape(tmp_path: Path) -> None:
    schema = StateSchema(name="bad", current_version=1)
    with pytest.raises(StateSchemaError, match="schema_version"):
        schema.validate({})

    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StateSchemaError, match="顶层必须是 JSON 对象"):
        load_state_file("session_config", path)

@pytest.mark.asyncio
async def test_runtime_response_capture_and_instance_print(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    assert _respond("captured", capture=True) == "captured"
    assert _respond("printed", capture=False) is None
    assert "printed" in capsys.readouterr().out
    assert "命令执行失败" in _capture_call(lambda: 1 / 0)

    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.instance_commands.cmd_instance_handler",
        lambda *_args, **_kwargs: print("instances"),
    )
    result = await handle_instance("/instance list", state={}, capture=False)
    assert result is None
    assert "instances" in capsys.readouterr().out
    assert escape_markdown_cell(" a|b\r\nc ") == "a\\|b c"
