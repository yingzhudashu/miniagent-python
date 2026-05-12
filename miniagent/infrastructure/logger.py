"""Mini Agent Python — 日志系统

提供两种日志能力：
1. 控制台日志：通过 get_logger() 获取标准 logging.Logger 实例
2. 结构化文件日志：通过 append_log() 追加 JSONL 格式到文件

控制台日志（写入 stderr；全屏 CLI 期间请配合 ``set_console_log_threshold(WARNING)``，
见 ``miniagent.engine.main.run_cli_loop``）：
    from miniagent.infrastructure.logger import get_logger
    logger = get_logger(__name__)
    logger.info("启动成功")
    logger.warning("配置缺失，使用默认值")
    logger.error("连接失败", exc_info=True)

JSONL 文件日志格式：
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
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# ─── 控制台日志 ─────────────────────────────────────────────

# 全局 logger 缓存，避免重复配置
_loggers: dict[str, logging.Logger] = {}

# StreamHandler 上生效的最低级别；全屏 TUI 期间应抬高，避免 stderr 仍破坏备用屏。
_console_handler_min_level: int = logging.INFO


def set_console_log_threshold(level: int) -> None:
    """调整所有通过 ``get_logger`` 注册的 StreamHandler 的最低输出级别。

    全屏 prompt_toolkit 下，日志无论写 stdout 还是 stderr，都会在集成终端里与 UI 交错，
    导致分层/重影；进入 TUI 时常用 ``logging.WARNING``，退出后恢复 ``logging.INFO``。

    Args:
        level: 如 ``logging.WARNING``、``logging.INFO``
    """
    global _console_handler_min_level
    _console_handler_min_level = level
    for lg in _loggers.values():
        for h in lg.handlers:
            if isinstance(h, logging.StreamHandler):
                stream = getattr(h, "stream", None)
                if stream in (sys.stdout, sys.stderr):
                    h.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """获取控制台 Logger 实例

    自动配置统一格式和颜色（如果终端支持）。
    同一 name 多次调用返回同一实例。

    Args:
        name: 模块名称，通常为 __name__

    Returns:
        配置好的 logging.Logger 实例

    Example:
        logger = get_logger(__name__)
        logger.info("模块已加载")
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)

    # 只在首次配置 handler
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        # 使用 stderr：全屏 prompt_toolkit 占用 stdout 备用屏，往 stdout 打日志会与 UI 分层/乱序。
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(_console_handler_min_level)

        # 统一格式：[时间] [级别] [模块] 消息
        fmt = "%(asctime)s [%(levelname)-7s] %(name)s — %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
        logger.addHandler(handler)

        # 阻止传播到 root logger（避免重复输出）
        logger.propagate = False

    _loggers[name] = logger
    return logger


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


__all__ = ["get_logger", "append_log", "truncate", "set_console_log_threshold"]
