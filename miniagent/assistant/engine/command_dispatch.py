"""Shared CLI and Feishu command normalization, lookup, and dispatch."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from typing import Any

from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.command_registry import COMMAND_REGISTRY, CommandHandler
from miniagent.assistant.engine.commands.basic_commands import (
    format_status as _format_status,  # noqa: F401 - internal compatibility alias
)
from miniagent.assistant.engine.commands.basic_commands import (
    handle_config,
    handle_doctor,
    handle_help,
    handle_model,
    handle_reload_config,
    handle_schedule,
    handle_stats,
    handle_status,
)
from miniagent.assistant.engine.commands.confirmation_commands import handle_confirmation
from miniagent.assistant.engine.commands.instance_commands import handle_instance
from miniagent.assistant.engine.commands.knowledge_commands import handle_knowledge
from miniagent.assistant.engine.commands.output import (  # noqa: F401
    capture_output as _capture,
)
from miniagent.assistant.engine.commands.quality_commands import (
    _get_last_qa,  # noqa: F401 - internal compatibility alias
    _run_improve,  # noqa: F401 - internal compatibility alias
    _run_review,  # noqa: F401 - internal compatibility alias
    handle_improve,
    handle_review,
)
from miniagent.assistant.engine.commands.runtime_commands import (
    handle_abort,
    handle_background_task,
    handle_feishu,
    handle_query,
    handle_queue,
    handle_reload_skills,
    handle_stop,
)
from miniagent.assistant.engine.commands.self_opt_commands import handle_self_opt
from miniagent.assistant.engine.commands.session_commands import handle_session
from miniagent.assistant.engine.commands.test_commands import (
    _get_test_status,  # noqa: F401 - internal compatibility alias
    _list_test_samples,  # noqa: F401 - internal compatibility alias
    _run_test,  # noqa: F401 - internal compatibility alias
    handle_test,
)

_REGISTERED_COMMANDS = list(COMMAND_REGISTRY.dispatch_names)


def _find_closest_command(input_cmd: str, threshold: float = 0.6) -> str | None:
    """Return the closest registered command above the similarity threshold."""
    matches = difflib.get_close_matches(
        input_cmd.lower(),
        [command.lower() for command in _REGISTERED_COMMANDS],
        n=1,
        cutoff=threshold,
    )
    if not matches:
        return None
    return next(
        (command for command in _REGISTERED_COMMANDS if command.lower() == matches[0]),
        None,
    )


def _find_command_by_prefix(input_cmd: str) -> str | None:
    """Return the first registered command matching a meaningful prefix."""
    lowered = input_cmd.lower()
    if len(lowered) < 4:
        return None
    return next(
        (command for command in _REGISTERED_COMMANDS if command.lower().startswith(lowered)),
        None,
    )


def _normalize_command_text(text: str) -> str | None:
    stripped = text.strip()
    return stripped if stripped.startswith("/") else None


_BOUND_HANDLERS: dict[str, CommandHandler] = {
    "abort": handle_abort,
    "adjust": handle_confirmation,
    "background_task": handle_background_task,
    "config": handle_config,
    "confirm": handle_confirmation,
    "doctor": handle_doctor,
    "feishu": handle_feishu,
    "help": handle_help,
    "improve": handle_improve,
    "instance": handle_instance,
    "knowledge": handle_knowledge,
    "model": handle_model,
    "query": handle_query,
    "queue": handle_queue,
    "reject": handle_confirmation,
    "reload_config": handle_reload_config,
    "reload_skills": handle_reload_skills,
    "review": handle_review,
    "schedule": handle_schedule,
    "self_opt": handle_self_opt,
    "session": handle_session,
    "stats": handle_stats,
    "status": handle_status,
    "stop": handle_stop,
    "test": handle_test,
}
BOUND_COMMAND_REGISTRY = COMMAND_REGISTRY.bind_handlers(_BOUND_HANDLERS)


async def dispatch_command(
    text: str,
    *,
    state: CliLoopState | dict[str, Any],
    engine: Any = None,
    registry: Any = None,
    monitor: Any = None,
    skill_toolboxes: list[Any] | None = None,
    skill_prompts: list[Any] | None = None,
    capture: bool = False,
    allow_session_mutations_when_capture: bool = True,
    feishu_user_status: Callable[[str], None] | None = None,
    message_queue_abort_chat_id: str | None = None,
    confirmation_session_key: str | None = None,
) -> str | None:
    """Resolve, authorize, and invoke one registered command."""
    normalized = _normalize_command_text(text)
    if normalized is None:
        return None
    command_name = normalized.split(maxsplit=1)[0].lower()
    handler = BOUND_COMMAND_REGISTRY.handler_for(command_name)
    if handler is not None:
        return await handler(
            normalized,
            state=state,
            engine=engine,
            registry=registry,
            monitor=monitor,
            skill_toolboxes=skill_toolboxes,
            skill_prompts=skill_prompts,
            capture=capture,
            allow_session_mutations_when_capture=allow_session_mutations_when_capture,
            feishu_user_status=feishu_user_status,
            message_queue_abort_chat_id=message_queue_abort_chat_id,
            confirmation_session_key=confirmation_session_key,
        )
    closest = _find_command_by_prefix(command_name) or _find_closest_command(command_name)
    if closest and closest.lower() != command_name:
        suggestion = f"{WARNING_PREFIX} 未找到命令 '{command_name}'，您是否想输入 '{closest}'？"
    else:
        suggestion = (
            f"{WARNING_PREFIX} 未找到命令 '{command_name}'。输入 /help 查看可用命令。"
        )
    if capture:
        return suggestion
    print(suggestion)
    return None


__all__ = ["BOUND_COMMAND_REGISTRY", "dispatch_command"]
