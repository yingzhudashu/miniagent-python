"""工具调用的用户可读意图摘要。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_TOOL_INTENT_MAP: dict[str, str] = {
    "read_file": "读取文件", "write_file": "写入文件", "edit_file": "编辑文件",
    "list_dir": "列出目录", "exec_command": "执行命令", "web_search": "搜索网页",
    "browser_extract_text": "浏览器提取正文", "fetch_url": "抓取网页",
    "read_memory": "读取记忆", "write_memory": "写入记忆", "search_memory": "搜索记忆",
    "git_status": "Git 状态", "git_diff": "Git 差异",
}


def extract_tool_intent(
    tool_name: str,
    args: dict[str, Any],
    *,
    max_chars: Callable[[], int],
) -> str:
    """按关键参数优先级生成有界工具意图摘要。"""
    base = _TOOL_INTENT_MAP.get(tool_name, f"调用 {tool_name}")
    for key in ("path", "query", "command", "content", "url"):
        if key not in args:
            continue
        value = str(args[key])
        cap = max_chars()
        if cap > 0 and len(value) > cap:
            value = value[:cap] + f"…（共 {len(value)} 字）"
        return f"{base}: {value}"
    return base


__all__ = ["extract_tool_intent"]
