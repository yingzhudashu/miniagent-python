"""model_cmd 模型切换与展示测试。"""

from __future__ import annotations

import json
from pathlib import Path

from miniagent.engine.model_cmd import (
    format_model_info,
    get_current_model,
    switch_model,
)
from miniagent.types.error_prefix import ERROR_PREFIX
from tests.config_helpers import install_test_config


def test_get_current_model_uses_user_override(tmp_path: Path) -> None:
    install_test_config(tmp_path, {"model": {"model": "custom-model"}})
    assert get_current_model() == "custom-model"


def test_get_current_model_empty_string_falls_back_to_default(tmp_path: Path) -> None:
    install_test_config(tmp_path, {"model": {"model": "   "}})
    assert get_current_model() == "gpt-4o-mini"


def test_switch_model_updates_config_and_preserves_other_sections(tmp_path: Path) -> None:
    user_path = tmp_path / "config.user.json"
    user_path.write_text(
        json.dumps(
            {
                "secrets": {"openai_api_key": "sk-test"},
                "model": {"model": "old-model", "base_url": "http://custom"},
            }
        ),
        encoding="utf-8",
    )
    install_test_config(tmp_path, user_path=user_path)

    result = switch_model("gpt-4o")
    assert "gpt-4o" in result
    assert ERROR_PREFIX not in result

    data = json.loads(user_path.read_text(encoding="utf-8"))
    assert data["secrets"]["openai_api_key"] == "sk-test"
    assert data["model"]["model"] == "gpt-4o"
    assert data["model"]["base_url"] == "http://custom"
    assert get_current_model() == "gpt-4o"


def test_switch_model_creates_user_file_when_missing(tmp_path: Path) -> None:
    user_path = tmp_path / "config.user.json"
    install_test_config(tmp_path, user_path=user_path)

    assert not user_path.exists()
    result = switch_model("new-model")
    assert ERROR_PREFIX not in result
    assert user_path.exists()
    assert json.loads(user_path.read_text())["model"]["model"] == "new-model"


def test_switch_model_rejects_empty_name(tmp_path: Path) -> None:
    install_test_config(tmp_path)
    result = switch_model("   ")
    assert result.startswith(ERROR_PREFIX)
    assert "不能为空" in result


def test_switch_model_corrupt_json_does_not_wipe_file(tmp_path: Path) -> None:
    user_path = tmp_path / "config.user.json"
    user_path.write_text("{bad json", encoding="utf-8")
    install_test_config(tmp_path, user_path=user_path)

    result = switch_model("any-model")
    assert result.startswith(ERROR_PREFIX)
    assert "格式无效" in result
    assert user_path.read_text(encoding="utf-8") == "{bad json"


def test_switch_model_rejects_non_dict_model_section(tmp_path: Path) -> None:
    user_path = tmp_path / "config.user.json"
    user_path.write_text(json.dumps({"model": "not-a-dict"}), encoding="utf-8")
    install_test_config(tmp_path, user_path=user_path)

    result = switch_model("gpt-4o")
    assert result.startswith(ERROR_PREFIX)
    assert "model` 节必须是对象" in result
    assert json.loads(user_path.read_text()) == {"model": "not-a-dict"}


def test_format_model_info_shows_current_model(tmp_path: Path) -> None:
    install_test_config(tmp_path, {"model": {"model": "shown-model"}})
    out = format_model_info()
    assert "`shown-model`" in out
    assert "/model gpt-4o" in out
    assert "持久化到 `config.user.json`" in out
