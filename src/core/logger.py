"""Mini Agent Python — 增量日志写入器

将 Agent 运行过程中的 LLM 输入/输出增量追加到 JSONL 文件。
每行一个 JSON 对象，方便后续解析或 tail -f 实时观察。

日志格式（每行 JSON）：
{
    "ts": "2026-05-01T08:00:00.000Z",
    "phase": "plan" | "exec",
    "turn": 1,
    "req": { "messages": [...], "model": "gpt-4o", "temperature": 0.7 },
    "res": { "content": "...", "usage": { ... } },
    "err": "..."  # 异常时才有
}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def append_log(log_file: str, entry: dict[str, Any]) -> None:
    """追加一条日志到 JSONL 文件

    每行追加一个 JSON 对象，自动附加 ISO 8601 时间戳。
    如果父目录不存在则自动创建。

    Args:
        log_file: 日志文件的完整路径
        entry: 要写入的日志条目（自动附加 ts 时间戳）

    Example:
        append_log('./logs/agent.jsonl', {
            'phase': 'exec',
            'turn': 1,
            'res': {'content': 'Hello!'}
        })
    """
    # 确保父目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    line = json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(), **entry},
        ensure_ascii=False,
    )
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def truncate(obj: Any, max_len: int = 2000) -> str:
    """安全截断大对象，避免日志文件膨胀

    将任意对象转为 JSON 字符串，超过 max_len 时截断并附加提示。

    Args:
        obj: 要格式化的对象
        max_len: 最大字符数（默认 2000）

    Returns:
        格式化后的字符串（可能被截断）

    Example:
        truncate({"large": "data" * 1000}, 50)
        # → '{\\n  "large": "datadatadat...\\n... [truncated, total N chars]'
    """
    s = obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False)
    if len(s) > max_len:
        return s[:max_len] + f"\n... [truncated, total {len(s)} chars]"
    return s


__all__ = ["append_log", "truncate"]
