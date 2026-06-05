"""Session-scoped NDJSON append for DEBUG_MODE (Cursor). Do not log secrets.

Debug logging is disabled unless debug.session_id is set in JSON config.
The log file path can be overridden via debug.log_path.

**注意**：本模块仅用于开发调试，日志可能包含工具参数与输出，请勿在生产环境启用。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

_SESSION = get_config("debug.session_id", "")
if _SESSION:
    _LOG = Path(
        get_config(
            "debug.log_path",
            str(Path(__file__).resolve().parents[2] / f"debug-{_SESSION}.log"),
        )
    )
else:
    _LOG = None


def agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
    run_id: str = "pre",
) -> None:
    """追加一行 NDJSON 调试日志（仅当设置了 MINIAGENT_DEBUG_SESSION_ID 时生效）。

    Args:
        hypothesis_id: 假设/提案 ID
        location: 调用位置标识
        message: 调试消息
        data: 附加数据（字典）
        run_id: 运行阶段标识，默认 "pre"
    """
    if not _SESSION:
        return
    try:
        line = json.dumps(
            {
                "sessionId": _SESSION,
                "runId": run_id,
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data or {},
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        _logger.debug("写入调试日志失败: %s", e)


def safe_agent_debug_log(
    *,
    location: str,
    message: str,
    data: dict | None = None,
    hypothesis_id: str = "B",
    run_id: str = "pre",
) -> None:
    """安全调用 agent_debug_log，自动填充常用参数，异常时静默。

    相比 ``agent_debug_log``，此函数：
    - 默认 hypothesis_id="B"（Agent 核心流程）
    - 内置 try-except，调用方无需嵌套处理
    - 调用前检查 _SESSION，避免无效调用

    Args:
        location: 调用位置标识（如 "planner.request"）
        message: 调试消息
        data: 附加数据（字典）
        hypothesis_id: 假设 ID（默认 "B"）
        run_id: 运行阶段标识（默认 "pre"）

    Example:
        # 替换原有重复模式
        safe_agent_debug_log(location="planner.request", message="LLM调用", data={"model": model})
    """
    if not _SESSION:
        return
    try:
        agent_debug_log(
            hypothesis_id=hypothesis_id,
            location=location,
            message=message,
            data=data or {},
            run_id=run_id,
        )
    except Exception as e:
        _logger.debug("安全调试日志调用失败: %s", e)


__all__ = ["agent_debug_log", "safe_agent_debug_log"]
