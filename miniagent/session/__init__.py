"""会话管理模块（磁盘布局 + 运行时 ``SessionManager``）

- ``SessionManager``：实际类名为 ``DefaultSessionManager``，此处导出为 ``SessionManager`` 别名；
  负责编号↔ID、切换会话、历史路径与锁（见 ``manager.py``）。
- ``WorkspaceManager``：会话目录下 ``files/``、``skills/`` 等工作区生命周期（见 ``workspace.py``）。

与 ``miniagent.types.memory.SessionManagerProtocol`` 对齐的是 ``DefaultSessionManager`` 的公开行为。
"""

from miniagent.session.manager import DefaultSessionManager as SessionManager
from miniagent.session.workspace import WorkspaceManager

__all__ = ["SessionManager", "WorkspaceManager"]
