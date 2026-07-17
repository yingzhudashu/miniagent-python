"""统一命令注册表的完整性与解析测试。"""

from __future__ import annotations

import pytest

from miniagent.assistant.engine.command_dispatch import _REGISTERED_COMMANDS, BOUND_COMMAND_REGISTRY
from miniagent.assistant.engine.command_registry import (
    COMMAND_REGISTRY,
    CommandRegistry,
    CommandSpec,
)


def test_legacy_command_list_is_derived_from_registry() -> None:
    assert _REGISTERED_COMMANDS == list(COMMAND_REGISTRY.dispatch_names)
    assert len(COMMAND_REGISTRY.names) == len(set(COMMAND_REGISTRY.names))
    assert "/copy" in COMMAND_REGISTRY.names_for("cli")
    assert "/copy" not in COMMAND_REGISTRY.names_for("feishu")


def test_every_command_has_handler_help_and_channel_policy() -> None:
    for spec in COMMAND_REGISTRY.specs:
        handler = BOUND_COMMAND_REGISTRY.handler_for(spec.name)
        if spec.owner == "dispatcher":
            assert spec.handler_key
            assert callable(handler)
            assert handler.__module__.startswith("miniagent.assistant.engine.commands.")
        else:
            assert spec.handler_key is None
            assert handler is None
        assert spec.summary
        assert spec.usage.startswith(spec.name)
        assert spec.channels

    query = COMMAND_REGISTRY.resolve("/query")
    assert query is not None
    assert "queue status" in query.summary


def test_prefix_priority_preserves_existing_stats_before_status() -> None:
    assert COMMAND_REGISTRY.first_prefix_match("/sta").name == "/stats"


def test_registry_rejects_duplicate_alias() -> None:
    with pytest.raises(ValueError, match="重复命令"):
        CommandRegistry(
            (
                CommandSpec("/one", "one", "one", "/one", aliases=("/shared",)),
                CommandSpec("/two", "two", "two", "/two", aliases=("/shared",)),
            )
        )


def test_registry_resolves_canonical_name_and_unknown() -> None:
    assert COMMAND_REGISTRY.resolve("/HELP").handler_key == "help"
    assert COMMAND_REGISTRY.resolve("/missing") is None


def test_command_spec_rejects_invalid_names_and_incomplete_metadata() -> None:
    with pytest.raises(ValueError, match="/ 前缀"):
        CommandSpec("help", "help", "help", "help")
    with pytest.raises(ValueError, match="元数据不完整"):
        CommandSpec("/empty", "empty", "", "/empty")
    with pytest.raises(ValueError, match="缺少处理器"):
        CommandSpec("/empty", None, "summary", "/empty")
    with pytest.raises(ValueError, match="不得绑定"):
        CommandSpec("/copy", "copy", "copy", "/copy", owner="cli_frontend")
    with pytest.raises(ValueError, match="只能声明 cli"):
        CommandSpec("/copy", None, "copy", "/copy", owner="cli_frontend")


def test_handler_binding_rejects_missing_and_extra_keys() -> None:
    async def handler(*_args, **_kwargs) -> None:
        return None

    with pytest.raises(ValueError, match="绑定不完整"):
        COMMAND_REGISTRY.bind_handlers({"help": handler})


def test_bound_registry_is_read_only_and_resolves_case_insensitively() -> None:
    assert callable(BOUND_COMMAND_REGISTRY.handler_for("/HELP"))
    assert BOUND_COMMAND_REGISTRY.handler_for("/missing") is None
    with pytest.raises(TypeError):
        BOUND_COMMAND_REGISTRY.handlers["help"] = None  # type: ignore[index, assignment]
