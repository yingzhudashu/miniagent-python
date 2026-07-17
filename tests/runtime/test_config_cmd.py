"""`/config` 配置查看命令测试。"""

from __future__ import annotations

import pytest

from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.engine.config_cmd import (
    _append_value_lines,
    _is_sensitive_key,
    _mask_sensitive,
    _summarize_value,
    format_config_info,
)


def test_is_sensitive_key_does_not_match_keyword_prefix() -> None:
    assert not _is_sensitive_key("keyword_index_max")
    assert not _is_sensitive_key("keyword_prune_interval")
    assert _is_sensitive_key("openai_api_key")
    assert _is_sensitive_key("feishu_app_secret")
    assert _is_sensitive_key("feishu_verification_token")


def test_mask_sensitive_truncates_long_strings() -> None:
    assert _mask_sensitive("openai_api_key", "sk-abcdefghijklmnop") == "sk-abcde..."


def test_append_value_lines_nested_dict() -> None:
    lines: list[str] = []
    _append_value_lines(
        lines,
        "loop_detection",
        {
            "enabled": True,
            "detectors": {
                "generic_repeat": True,
                "ping_pong": False,
            },
        },
    )
    out = "\n".join(lines)
    assert "- `loop_detection`:" in out
    assert "  - `detectors`:" in out
    assert "    - `generic_repeat`: `True`" in out
    assert "    - `ping_pong`: `False`" in out


def test_append_value_lines_list_preview() -> None:
    lines: list[str] = []
    _append_value_lines(lines, "hosts", ["a.example.com", "b.example.com", "c.example.com"])
    out = "\n".join(lines)
    assert "- `hosts`: `3 项`" in out
    assert "  - `[0]`: `a.example.com`" in out
    assert "  - `[2]`: `c.example.com`" in out


def test_append_value_lines_truncates_long_list() -> None:
    lines: list[str] = []
    _append_value_lines(lines, "items", list(range(12)), max_list_items=3)
    out = "\n".join(lines)
    assert "- `items`: `12 项`" in out
    assert "  - `[2]`: `2`" in out
    assert "  - ... (共 12 项)" in out
    assert "  - `[3]`" not in out


def test_summarize_value_scalar_list() -> None:
    assert _summarize_value("tags", ["a", "b"]) == "2 项: a, b"
    assert _summarize_value("tags", []) == "0 项"
    assert _summarize_value("nested", [{"x": 1}]) == "1 项"


def test_format_config_info_memory_shows_keyword_fields(isolated_config_loader) -> None:
    isolated_config_loader()
    out = format_config_info("memory")
    assert "`keyword_index_max`: `20000`" in out
    assert "`keyword_prune_interval`: `86400`" in out


def test_format_config_info_agent_nested_dict(isolated_config_loader) -> None:
    isolated_config_loader()
    out = format_config_info("agent")
    assert "  - `detectors`:" in out
    assert "    - `generic_repeat`: `True`" in out


def test_format_config_info_list_section(isolated_config_loader) -> None:
    isolated_config_loader(
        {"cli": {"dot_tools_enabled": True, "extra_hosts": ["host-a", "host-b"]}}
    )
    with pytest.raises(ValueError, match="未知配置项"):
        format_config_info("cli")


def test_format_config_info_overview_includes_agent_html(isolated_config_loader) -> None:
    isolated_config_loader()
    out = format_config_info()
    assert "#### agent_html" in out


def test_format_config_info_overview_shows_empty_section(
    monkeypatch,
    isolated_config_loader,
) -> None:
    isolated_config_loader()
    from miniagent.assistant.infrastructure import json_config

    real_get = json_config.get_config_section

    def fake_get(section: str):
        if section == "scheduled_tools":
            return {}
        return real_get(section)

    monkeypatch.setattr(json_config, "get_config_section", fake_get)
    out = format_config_info()
    assert "#### scheduled_tools" in out
    assert "当前无配置项" in out


def test_format_config_info_agent_html_shows_user_layer_hint(isolated_config_loader) -> None:
    isolated_config_loader()
    out = format_config_info("agent_html")
    assert "（User 层，可在 config.user.json 中覆盖）" in out


def test_format_config_info_memory_shows_advanced_layer_hint(isolated_config_loader) -> None:
    isolated_config_loader()
    out = format_config_info("memory")
    assert "（Advanced 运维默认值，一般无需写入 config.user.json）" in out


def test_format_config_info_unknown_section(isolated_config_loader) -> None:
    isolated_config_loader()
    out = format_config_info("totally_fake_section")
    assert f"{WARNING_PREFIX} 配置部分 `totally_fake_section` 不存在或为空" in out


def test_format_config_info_masks_secrets(isolated_config_loader) -> None:
    isolated_config_loader({
        "secrets": {
            "llm": {"openai": {"api_key": "sk-testsecretvalue12345"}},
        },
    })
    out = format_config_info("secrets")
    assert "sk-tests..." in out
    assert "sk-testsecretvalue12345" not in out
