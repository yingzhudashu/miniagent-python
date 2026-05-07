"""Mini Agent Python — 会话管理模块

提供多会话隔离机制：
- SessionManager: 会话创建/切换/销毁
- WorkspaceManager: 文件系统隔离
每个会话拥有独立的工作空间、工具注册表和对话历史。
"""

from src.session.manager import SessionManager
from src.session.workspace import WorkspaceManager

__all__ = ["SessionManager", "WorkspaceManager"]
