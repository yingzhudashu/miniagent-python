"""会话管理模块（磁盘布局 + 运行时 ``DefaultSessionManager``）

- ``DefaultSessionManager``：负责编号↔ID、切换会话、历史路径与锁（见 ``manager.py``）。
- ``WorkspaceManager``：会话目录下 ``files/``、``skills/`` 等工作区生命周期（见 ``workspace.py``）。

与 ``miniagent.agent.types.memory.SessionManagerProtocol`` 对齐的是 ``DefaultSessionManager`` 的公开行为。

会话与 Engine 的衔接见 ``docs/ARCHITECTURE.md``；状态目录与多实例见 ``docs/ENGINEERING.md`` §3.3。
"""

from miniagent.assistant.session.manager import DefaultSessionManager
from miniagent.assistant.session.workspace import WorkspaceManager

__all__ = ["DefaultSessionManager", "WorkspaceManager"]
