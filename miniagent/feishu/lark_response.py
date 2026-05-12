"""飞书 lark-oapi 响应上的可读错误摘要（供日志与工具返回）。"""

from __future__ import annotations

from typing import Any


def format_lark_response_error(resp: Any) -> str:
    """从 SDK 响应对象提取 ``code`` / ``msg`` / ``log_id``（若存在）。"""
    code = getattr(resp, "code", None)
    msg = getattr(resp, "msg", None)
    parts: list[str] = []
    if code is not None:
        parts.append(f"code={code}")
    if msg is not None and str(msg):
        parts.append(f"msg={msg}")
    log_id = getattr(resp, "log_id", None)
    if log_id is not None and str(log_id):
        parts.append(f"log_id={log_id}")
    return " ".join(parts) if parts else "unknown_error"
