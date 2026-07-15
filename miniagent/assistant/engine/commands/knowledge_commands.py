"""知识库命令的参数解析与渠道输出适配。"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, WARNING_PREFIX


def _capture(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """捕获知识库叶子命令输出，并保留可操作错误。"""
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = callable_(*args, **kwargs)
    except Exception as error:
        return f"{ERROR_PREFIX} 命令执行失败: {error}"
    return str(result) if isinstance(result, str) else buffer.getvalue().strip()


async def handle_knowledge(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """列出、挂载、卸载、搜索或重载知识库。"""
    from miniagent.assistant.engine.cli_commands import (
        cmd_kb_list,
        cmd_kb_mount,
        cmd_kb_reload,
        cmd_kb_search,
        cmd_kb_unmount,
        feishu_markdown_commands_enabled,
        format_kb_command_usage,
    )

    runtime = state.get("runtime_ctx")
    if runtime is None:
        output = f"{WARNING_PREFIX} 运行时上下文未初始化"
    else:
        parts = text.split()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        registry = runtime.knowledge_registry
        markdown = capture and feishu_markdown_commands_enabled()
        if subcommand in {"", "list"}:
            output = _capture(cmd_kb_list, registry, markdown=markdown)
        elif subcommand == "mount" and len(parts) >= 3:
            output = _capture(
                cmd_kb_mount,
                registry,
                parts[2],
                parts[3] if len(parts) > 3 else None,
            )
        elif subcommand == "unmount" and len(parts) >= 3:
            output = _capture(cmd_kb_unmount, registry, parts[2])
        elif subcommand == "search" and len(parts) >= 3:
            query, kb_name = _parse_search(parts, registry)
            output = _capture(cmd_kb_search, registry, query, kb_name)
        elif subcommand == "reload":
            output = _capture(cmd_kb_reload, registry, parts[2] if len(parts) > 2 else None)
        else:
            output = format_kb_command_usage()
    if capture:
        return output
    print(output)
    return None


def _parse_search(parts: list[str], registry: Any) -> tuple[str, str | None]:
    """从搜索尾参数中识别可选知识库名称。"""
    query = " ".join(parts[2:])
    names = {item["name"] for item in registry.list() if "name" in item}
    if len(parts) >= 4 and parts[-1] in names:
        return " ".join(parts[2:-1]), parts[-1]
    return query, None


__all__ = ["handle_knowledge"]
