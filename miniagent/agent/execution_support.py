"""Leaf helpers shared by the executor, turn streamer, and tool runner."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from miniagent.agent.constants import (
    EXECUTION_TOOL_INTENT_IN_THINKING,
    EXECUTION_TOOL_INTENT_MAX_CHARS,
)
from miniagent.agent.context import ContextBudgetExceeded, DefaultContextManager
from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import WARNING_PREFIX

logger = get_logger("miniagent.agent.executor")
EXEC_LLM_MAX_ATTEMPTS = 3

_TOOL_INTENT_MAP: dict[str, str] = {
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    "list_dir": "列出目录",
    "exec_command": "执行命令",
    "web_search": "搜索网页",
    "browser_extract_text": "浏览器提取正文",
    "fetch_url": "抓取网页",
    "read_memory": "读取记忆",
    "write_memory": "写入记忆",
    "search_memory": "搜索记忆",
    "git_status": "Git 状态",
    "git_diff": "Git 差异",
}


def exec_retry_params(base: dict[str, Any], *, attempt: int, responses: bool) -> dict[str, Any]:
    """Derive one retry request without mutating the caller's base parameters."""
    params = dict(base)
    if not responses or attempt == 0:
        return params
    params.pop("temperature", None)
    params.pop("top_p", None)
    params["_omit_parameters"] = ("temperature", "top_p")
    if attempt == EXEC_LLM_MAX_ATTEMPTS - 1:
        params["_thinking_level"] = "medium"
    return params


def raise_if_task_cancelled() -> None:
    """Propagate cancellation before entering another expensive execution step."""
    task = asyncio.current_task()
    if task is not None and task.cancelled():
        raise asyncio.CancelledError()


def append_context_or_error(
    context_manager: DefaultContextManager,
    message: dict[str, Any],
) -> str | None:
    """Append context and convert budget exhaustion to a user-facing warning."""
    try:
        context_manager.append(message)
    except ContextBudgetExceeded as error:
        return f"{WARNING_PREFIX} {error}"
    return None


@lru_cache(maxsize=1)
def tool_intent_in_thinking_enabled() -> bool:
    """Return the process-cached tool-intent display switch."""
    return EXECUTION_TOOL_INTENT_IN_THINKING


@lru_cache(maxsize=1)
def _tool_intent_max_chars() -> int:
    return EXECUTION_TOOL_INTENT_MAX_CHARS


def reset_tool_intent_caches() -> None:
    """Invalidate display caches after configuration replacement in tests."""
    tool_intent_in_thinking_enabled.cache_clear()
    _tool_intent_max_chars.cache_clear()


def extract_tool_intent(tool_name: str, args: dict[str, Any]) -> str:
    """Build a bounded, human-readable intent without serializing all arguments."""
    base = _TOOL_INTENT_MAP.get(tool_name, f"调用 {tool_name}")
    for key in ("path", "query", "command", "content", "url"):
        if key not in args:
            continue
        value = str(args[key])
        cap = _tool_intent_max_chars()
        if cap > 0 and len(value) > cap:
            value = value[:cap] + f"…（共 {len(value)} 字）"
        return f"{base}: {value}"
    return base


__all__ = [
    "EXEC_LLM_MAX_ATTEMPTS",
    "append_context_or_error",
    "exec_retry_params",
    "extract_tool_intent",
    "logger",
    "raise_if_task_cancelled",
    "reset_tool_intent_caches",
    "tool_intent_in_thinking_enabled",
]
