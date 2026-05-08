"""Activity Log — 每日详细活动记录

写入 memory/YYYY-MM-DD.md，三层记忆架构的 Layer 2（流水账）。
记录每次 LLM 调用、工具调用详情、思考过程。

格式：
## 会话 <session_key>
### 用户输入
...
### LLM 调用 (第 N 轮)
- model: gpt-4o-mini
- tokens: 1234
- thinking: ...
### 工具调用
- tool: read_file
- intent: 读取配置文件
- args: {"path": "config.json"}
- result: {...} (前 500 字)
### 最终回复
...
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


class ActivityLogger:
    """每日活动日志，追加写入 Markdown 文件。"""

    def __init__(self, base_dir: str = "memory") -> None:
        self._base_dir = base_dir

    def _get_today_path(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(self._base_dir, exist_ok=True)
        return os.path.join(self._base_dir, f"{today}.md")

    def _read_today(self) -> str:
        path = self._get_today_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _append(self, content: str) -> None:
        path = self._get_today_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)

    def log_session_start(self, session_key: str, user_input: str, source: str = "cli") -> None:
        """记录会话开始。"""
        today = self._read_today()
        header = f"\n---\n## {session_key} ({source})\n\n"
        if f"## {session_key}" not in today:
            self._append(header)
        self._append(f"### 用户输入\n\n{user_input}\n\n")

    def log_llm_call(
        self,
        session_key: str,
        turn: int,
        model: str,
        message_count: int,
        tool_count: int,
        thinking: str | None,
        token_usage: dict | None = None,
    ) -> None:
        """记录 LLM 调用详情。"""
        lines = [f"### LLM 调用 (第 {turn} 轮)\n"]
        lines.append(f"- model: {model}")
        lines.append(f"- messages: {message_count}, tools: {tool_count}")
        if token_usage:
            lines.append(f"- tokens: prompt={token_usage.get('prompt_tokens', '?')}, completion={token_usage.get('completion_tokens', '?')}")
        if thinking:
            lines.append(f"- thinking: {thinking[:500]}")
        lines.append("")
        self._append("\n".join(lines))

    def log_tool_call(
        self,
        session_key: str,
        tool_name: str,
        intent: str,
        args: dict[str, Any],
        result: str,
        duration_ms: int,
        success: bool,
    ) -> None:
        """记录工具调用详情。"""
        status = "ok" if success else "fail"
        lines = [f"### 工具调用: {tool_name} [{status}]\n"]
        lines.append(f"- intent: {intent}")
        lines.append(f"- args: {_short_json(args)}")
        # 结果截断到 500 字
        preview = result[:500]
        if len(result) > 500:
            preview += f"\n... (共 {len(result)} 字)"
        lines.append(f"- result: {preview}")
        lines.append(f"- duration: {duration_ms}ms")
        lines.append("")
        self._append("\n".join(lines))

    def log_final_reply(self, session_key: str, reply: str) -> None:
        """记录最终回复。"""
        self._append(f"### 最终回复\n\n{reply[:1000]}\n\n")

    def log_incomplete(self, session_key: str, reason: str) -> None:
        """记录未完成（达到最大轮数）。"""
        self._append(f"### 未完成\n\n{reason}\n\n")


def _short_json(data: Any, max_len: int = 200) -> str:
    """简短 JSON 表示。"""
    s = json.dumps(data, ensure_ascii=False)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


# 全局单例
activity_log = ActivityLogger()
