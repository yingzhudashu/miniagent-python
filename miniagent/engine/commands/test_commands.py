"""内置行为评测命令的参数解析处理器。"""

from __future__ import annotations

from typing import Any


async def handle_test(
    text: str,
    *,
    state: dict[str, Any],
    engine: Any = None,
    registry: Any = None,
    monitor: Any = None,
    skill_toolboxes: list[Any] | None = None,
    skill_prompts: list[str] | None = None,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """运行、列出或查询内置评测样例。"""
    from miniagent.engine.cli_commands import format_test_command_usage
    from miniagent.engine.command_dispatch import (
        _get_test_status,
        _list_test_samples,
        _run_test,
    )

    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    runtime = state.get("runtime_ctx")
    if subcommand == "run":
        mode, category, name_pattern = _parse_run_arguments(parts)
        output = await _run_test(
            category=category,
            name_pattern=name_pattern,
            mock=mode != "real",
            engine=engine,
            registry=registry,
            monitor=monitor,
            skill_toolboxes=skill_toolboxes,
            skill_prompts="\n".join(skill_prompts) if skill_prompts else None,
            state=state,
            term_write=getattr(runtime, "cli_transcript_append", None),
            capture=capture,
        )
    elif subcommand == "list":
        output = _list_test_samples()
    elif subcommand == "status":
        output = _get_test_status()
    else:
        output = format_test_command_usage()
    if capture:
        return output
    if output:
        print(output)
    return None


def _parse_run_arguments(parts: list[str]) -> tuple[str, str | None, str | None]:
    """解析 ``/test run [mock|real] [category] [pattern]``。"""
    if len(parts) > 2 and parts[2].lower() in {"mock", "real"}:
        return (
            parts[2].lower(),
            parts[3] if len(parts) > 3 else None,
            parts[4] if len(parts) > 4 else None,
        )
    return (
        "mock",
        parts[2] if len(parts) > 2 else None,
        parts[3] if len(parts) > 3 else None,
    )


__all__ = ["handle_test"]
