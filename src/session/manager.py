"""Mini Agent Python — 多会话管理器

每个会话拥有独立的工作空间、工具注册表、技能、记忆。
会话间默认完全隔离，除非显式"升维"才共享到主空间。

工作空间结构：
    state/
    ├── workspaces/
    │   └── <sessionId>/
    │       ├── files/        — 会话文件（工具操作默认目录）
    │       ├── skills/       — 会话级技能
    │       └── config.json   — 会话配置
    ├── memory/
    │   ├── <sessionId>.json
    │   └── keyword-index.json
    └── instance.pid
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.types.tool import ToolDefinition, ToolContext, Toolbox, RegisteredTool
from src.types.skill import Skill
from src.types.memory import Session, SessionOptions, SessionManagerProtocol
from src.core.registry import DefaultToolRegistry
from src.core.logger import get_logger

_logger = get_logger(__name__)


# ============================================================================
# 路径
# ============================================================================

def _get_state_dir() -> str:
    """获取状态目录"""
    return os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "state"))


def _get_workspaces_dir() -> str:
    """获取工作空间目录"""
    return os.path.join(_get_state_dir(), "workspaces")


# ============================================================================
# 会话配置
# ============================================================================

@dataclass
class SessionConfig:
    """会话配置

    Attributes:
        session_id: 会话 ID
        workspace_path: 工作空间路径
        files_path: 文件目录（工具操作默认位置）
        skills_path: 技能目录
        created_at: 创建时间
        last_active: 最后活跃时间
        description: 描述
        chat_id: 关联的 chatId
        sender_id: 关联的 senderId
    """
    session_id: str
    workspace_path: str
    files_path: str
    skills_path: str
    created_at: str
    last_active: str
    description: str = ""
    chat_id: str | None = None
    sender_id: str | None = None


@dataclass
class SessionInfo:
    """会话信息（用于列表展示）"""
    session_id: str
    description: str
    created_at: str
    last_active: str
    tool_count: int
    skill_count: int
    files_path: str


# ============================================================================
# SessionManager
# ============================================================================

class DefaultSessionManager(SessionManagerProtocol):
    """多会话管理器

    职责：
    1. 每个会话独立的工作空间、工具注册表、技能
    2. 会话隔离，默认不共享
    3. "升维"机制：将工具/技能提升到主空间（所有会话可见）
    4. 核心工具自动克隆到新会话

    Example:
        manager = DefaultSessionManager(main_registry)
        session = manager.get_or_create("session-1")
        tools = manager.list()
        manager.promote_tool("session-1", "new_tool")
    """

    def __init__(
        self,
        main_registry: DefaultToolRegistry,
        main_toolboxes: list[Toolbox] | None = None,
        main_skills: list[Skill] | None = None,
    ) -> None:
        """创建会话管理器

        Args:
            main_registry: 主空间工具注册表
            main_toolboxes: 主空间工具箱列表
            main_skills: 主空间技能列表
        """
        self._sessions: dict[str, dict] = {}  # sessionId -> context
        self._main_registry = main_registry
        self._main_toolboxes: list[Toolbox] = main_toolboxes or []
        self._main_skills: list[Skill] = main_skills or []
        self._active_session_id: str | None = None
        self._ensure_workspaces_dir()

    def _ensure_workspaces_dir(self) -> None:
        """确保工作空间目录存在"""
        os.makedirs(_get_workspaces_dir(), exist_ok=True)

    def _make_safe_id(self, session_id: str) -> str:
        """将非法路径字符替换为安全字符

        Args:
            session_id: 原始会话 ID

        Returns:
            安全的会话 ID
        """
        return re.sub(r'[<>:"/\\|?*]', "_", session_id)

    def _save_config(self, config: SessionConfig) -> None:
        """保存会话配置到磁盘

        Args:
            config: 会话配置
        """
        try:
            config_path = os.path.join(config.workspace_path, "config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "session_id": config.session_id,
                        "workspace_path": config.workspace_path,
                        "files_path": config.files_path,
                        "skills_path": config.skills_path,
                        "created_at": config.created_at,
                        "last_active": config.last_active,
                        "description": config.description,
                        "chat_id": config.chat_id,
                        "sender_id": config.sender_id,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception:
            pass  # 忽略保存失败

    # -----------------------------------------------------------------------
    # 会话历史持久化
    # -----------------------------------------------------------------------

    def save_session_history(self, session_id: str) -> None:
        """持久化会话历史到磁盘

        Args:
            session_id: 会话 ID
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return
        try:
            history = ctx.get("conversation_history", [])
            path = os.path.join(ctx["config"].workspace_path, "history.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_session_history(self, session_id: str) -> list:
        """从磁盘加载会话历史

        Args:
            session_id: 会话 ID

        Returns:
            历史消息列表
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return []
        try:
            path = os.path.join(ctx["config"].workspace_path, "history.json")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def load_all_sessions(self) -> list[str]:
        """从磁盘加载所有已保存的会话 ID

        Returns:
            已保存的会话 ID 列表
        """
        workspaces = _get_workspaces_dir()
        ids = []
        if os.path.isdir(workspaces):
            for name in os.listdir(workspaces):
                config_path = os.path.join(workspaces, name, "config.json")
                if os.path.isfile(config_path):
                    ids.append(name.replace("_", "-"))  # 反向 safe_id
        return ids

    def get_or_create(
        self, id: str, options: SessionOptions | None = None
    ) -> Session:
        """获取或创建会话

        - 已存在 → 返回现有会话
        - 不存在 → 检查工作空间配置是否存在 → 加载历史 + 创建

        Args:
            id: 会话唯一标识
            options: 可选配置

        Returns:
            会话对象
        """
        if id in self._sessions:
            ctx = self._sessions[id]
            ctx["config"].last_active = datetime.now(timezone.utc).isoformat()
            return ctx["session"]

        # 检查是否有持久化的工作空间（重启后恢复）
        safe_id = self._make_safe_id(id)
        workspace_path = os.path.join(_get_workspaces_dir(), safe_id)
        config_path = os.path.join(workspace_path, "config.json")
        if os.path.isfile(config_path):
            return self._restore(id, workspace_path, options)

        return self._create(id, options)

    def _restore(
        self, session_id: str, workspace_path: str, options: SessionOptions | None
    ) -> Session:
        """从磁盘恢复已有会话（含历史）"""
        config_path = os.path.join(workspace_path, "config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        config = SessionConfig(
            session_id=raw["session_id"],
            workspace_path=raw["workspace_path"],
            files_path=raw["files_path"],
            skills_path=raw["skills_path"],
            created_at=raw["created_at"],
            last_active=datetime.now(timezone.utc).isoformat(),
            description=raw.get("description", ""),
            chat_id=raw.get("chat_id"),
            sender_id=raw.get("sender_id"),
        )

        registry = DefaultToolRegistry()
        core_count = 0
        for name, tool in self._main_registry.get_all().items():
            if not tool.toolbox:
                try:
                    registry.register(name, ToolDefinition(
                        schema=tool.schema, handler=tool.handler,
                        permission=tool.permission, help_text=tool.help_text,
                        toolbox=tool.toolbox,
                    ))
                    core_count += 1
                except ValueError:
                    pass

        session = Session(
            id=session_id,
            description=config.description,
            created_at=config.created_at,
            last_active_at=config.last_active,
            workspace_path=config.files_path,
        )

        # 加载持久化的对话历史
        history_path = os.path.join(workspace_path, "history.json")
        if os.path.isfile(history_path):
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    conversation_history = json.load(f)
            except Exception:
                conversation_history = []
        else:
            conversation_history = []

        # 同步到 Session 对象
        session.conversation_history = list(conversation_history)

        ctx = {
            "session_id": session_id,
            "config": config,
            "registry": registry,
            "session": session,
            "toolboxes": [],
            "skills": [],
            "conversation_history": conversation_history,
        }
        self._sessions[session_id] = ctx

        _logger.info(
            "会话已恢复: %s (%d 个核心工具, %d 条历史)",
            session_id, core_count, len(conversation_history),
        )
        return session

    def _create(self, session_id: str, options: SessionOptions | None) -> Session:
        """创建新会话

        Args:
            session_id: 会话唯一标识
            options: 可选配置

        Returns:
            新创建的会话
        """
        safe_id = self._make_safe_id(session_id)
        workspace_path = os.path.join(_get_workspaces_dir(), safe_id)
        files_path = os.path.join(workspace_path, "files")
        skills_path = os.path.join(workspace_path, "skills")

        os.makedirs(files_path, exist_ok=True)
        os.makedirs(skills_path, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        config = SessionConfig(
            session_id=session_id,
            workspace_path=workspace_path,
            files_path=files_path,
            skills_path=skills_path,
            created_at=now,
            last_active=now,
            description=options.description if options else "",
        )

        self._save_config(config)

        # 会话级注册表
        registry = DefaultToolRegistry()

        # 克隆主空间的核心工具（无 toolbox 的 = 核心能力）
        core_count = 0
        for name, tool in self._main_registry.get_all().items():
            if not tool.toolbox:
                try:
                    registry.register(name, ToolDefinition(
                        schema=tool.schema,
                        handler=tool.handler,
                        permission=tool.permission,
                        help_text=tool.help_text,
                        toolbox=tool.toolbox,
                    ))
                    core_count += 1
                except ValueError:
                    pass  # 已存在，跳过

        # 创建 Session 对象
        session = Session(
            id=session_id,
            description=config.description,
            created_at=config.created_at,
            last_active_at=config.last_active,
            workspace_path=config.files_path,
        )

        ctx = {
            "session_id": session_id,
            "config": config,
            "registry": registry,
            "session": session,
            "toolboxes": options.toolboxes if options else [],
            "skills": [],
            "conversation_history": [],
        }

        self._sessions[session_id] = ctx

        _logger.info("会话已创建: %s (%d 个核心工具)", session_id, core_count)
        return session

    def get(self, id: str) -> Session | None:
        """获取会话

        Args:
            id: 会话 ID

        Returns:
            会话对象，不存在返回 None
        """
        ctx = self._sessions.get(id)
        return ctx["session"] if ctx else None

    def list(self) -> list[Session]:
        """列出所有活跃会话

        Returns:
            活跃会话列表
        """
        return [ctx["session"] for ctx in self._sessions.values()]

    def destroy(self, id: str, keep_files: bool = True) -> bool:
        """销毁会话

        Args:
            id: 要销毁的会话 ID
            keep_files: 是否保留工作空间文件（默认 True）

        Returns:
            成功返回 True，会话不存在返回 False
        """
        ctx = self._sessions.get(id)
        if not ctx:
            return False

        ctx["config"].last_active = datetime.now(timezone.utc).isoformat()
        self._save_config(ctx["config"])
        del self._sessions[id]

        if not keep_files:
            try:
                import shutil
                shutil.rmtree(ctx["config"].workspace_path, ignore_errors=True)
            except Exception:
                pass

        _logger.info("会话已销毁: %s", id)
        return True

    def get_active_id(self) -> str:
        """获取当前活跃会话 ID

        Returns:
            活跃会话 ID
        """
        return self._active_session_id or ""

    def set_active(self, id: str) -> bool:
        """切换活跃会话

        Args:
            id: 目标会话 ID

        Returns:
            成功返回 True，会话不存在返回 False
        """
        if id not in self._sessions:
            return False
        self._active_session_id = id
        return True

    def promote_tool(self, session_id: str, tool_name: str) -> bool:
        """工具升维：将会话中的工具提升到主空间

        升维后，所有新会话都能获得该工具。

        Args:
            session_id: 源会话 ID
            tool_name: 要升维的工具名称

        Returns:
            成功返回 True
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return False

        tool = ctx["registry"].get(tool_name)
        if not tool:
            return False

        try:
            self._main_registry.register(tool_name, ToolDefinition(
                schema=tool.schema,
                handler=tool.handler,
                permission=tool.permission,
                help_text=tool.help_text,
                toolbox=tool.toolbox,
            ))
            return True
        except ValueError:
            return False  # 已在主空间存在

    def demote_tool(self, session_id: str, tool_name: str) -> bool:
        """工具降维：从主空间移除工具

        移除后，所有会话不再看到该工具（除非会话级注册）。

        Args:
            session_id: 会话 ID（保留参数以匹配接口）
            tool_name: 要移除的工具名称

        Returns:
            成功返回 True
        """
        return self._main_registry.unregister(tool_name)

    # -----------------------------------------------------------------------
    # 工具执行上下文
    # -----------------------------------------------------------------------

    def get_tool_context(self, session_id: str) -> ToolContext:
        """获取会话的工具执行上下文

        Args:
            session_id: 会话 ID

        Returns:
            工具执行上下文，包含 cwd、allowed_paths、permission
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            default_workspace = os.environ.get(
                "MINI_AGENT_WORKSPACE", os.getcwd()
            )
            return ToolContext(
                cwd=default_workspace,
                allowed_paths=[default_workspace],
                permission="allowlist",
            )

        return ToolContext(
            cwd=ctx["config"].files_path,
            allowed_paths=[ctx["config"].files_path],
            permission="allowlist",
        )

    # -----------------------------------------------------------------------
    # 会话级工具管理
    # -----------------------------------------------------------------------

    def register_tool(
        self, session_id: str, name: str, tool: ToolDefinition
    ) -> bool:
        """在会话中注册工具

        Args:
            session_id: 目标会话 ID
            name: 工具名称
            tool: 工具定义

        Returns:
            成功返回 True，会话不存在返回 False
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return False
        try:
            ctx["registry"].register(name, tool)
            return True
        except ValueError:
            return False

    def unregister_tool(self, session_id: str, name: str) -> bool:
        """从会话注销工具

        Args:
            session_id: 目标会话 ID
            name: 工具名称

        Returns:
            成功返回 True
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return False
        return ctx["registry"].unregister(name)

    # -----------------------------------------------------------------------
    # 主空间查询
    # -----------------------------------------------------------------------

    def get_main_tools(self) -> list[str]:
        """获取主空间所有工具名称

        Returns:
            工具名称列表
        """
        return self._main_registry.list()

    def get_main_skills(self) -> list[Skill]:
        """获取主空间所有技能

        Returns:
            技能列表（副本）
        """
        return list(self._main_skills)

    def get_main_toolboxes(self) -> list[Toolbox]:
        """获取主空间所有工具箱

        Returns:
            工具箱列表（副本）
        """
        return list(self._main_toolboxes)

    def get_main_registry(self) -> DefaultToolRegistry:
        """获取主空间工具注册表

        Returns:
            主空间的 ToolRegistry 实例
        """
        return self._main_registry


__all__ = ["DefaultSessionManager", "SessionManager", "SessionConfig", "SessionInfo"]

# Compatibility alias
SessionManager = DefaultSessionManager
