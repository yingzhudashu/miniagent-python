"""MiniAgent 工具函数集合

本包提供跨模块共享的通用工具函数，消除代码重复。

模块列表：
- session_id: Session ID 安全化处理（统一各模块的安全化逻辑）
- error_handling: 统一错误处理装饰器（safe_execute、log_exception）

使用示例：
    >>> from miniagent.assistant.utils import safe_session_id, safe_execute
    >>> safe_id = safe_session_id("user@example.com")  # -> "user_example_com"
    >>> @safe_execute(default_return=None)
    >>> async def load_file(path: str) -> str:
    >>>     with open(path) as f:
    >>>         return f.read()
"""

from __future__ import annotations

from miniagent.assistant.utils.error_handling import log_exception, safe_execute, safe_execute_sync
from miniagent.assistant.utils.session_id import safe_session_id

__all__ = ["safe_session_id", "safe_execute", "safe_execute_sync", "log_exception"]