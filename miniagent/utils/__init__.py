"""MiniAgent 工具函数集合

本包提供跨模块共享的通用工具函数，消除代码重复。

模块列表：
- session_id: Session ID 安全化处理（统一各模块的安全化逻辑）
"""

from __future__ import annotations

from miniagent.utils.session_id import safe_session_id

__all__ = ["safe_session_id"]