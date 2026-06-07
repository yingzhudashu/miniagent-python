"""Mini Agent Python — 记忆与会话管理类型

描述三层记忆 **概念模型** 与 ``SessionManagerProtocol`` 等契约；具体存储、索引与
管线逻辑在 ``miniagent.memory``（如 ``store``、``keyword_index``、``memory_pipeline``）。

层次对应关系（概念）：

- Layer 1: 当前对话窗口（与 ``miniagent.memory.context`` 协同）
- Layer 2: 会话级持久记忆（如 ``DefaultMemoryStore``）
- Layer 3: 跨会话检索（如 ``KeywordIndex``）

**Protocol 最佳实践**：
- Protocol 不使用 @abstractmethod（Python Protocol 仅定义方法签名，实现类自行提供）
- 使用 @runtime_checkable 支持 isinstance() 检查
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GroundTruthFact:
    """可追溯的长期确定事实。

    ``key_facts`` 保留为兼容性的字符串摘要；本类型用于保存可更新、可纠正、
    可作为后续需求自澄清依据的稳定事实。``supersedes`` 记录被当前事实替换的
    旧值，避免在用户纠正偏好或约束后继续使用过期信息。
    """

    key: str
    value: str
    category: str = "preference"
    confidence: float = 1.0
    source: str = "user"
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    supersedes: str | None = None
    evidence: str = ""


@dataclass
class MemoryEntry:
    """记忆条目：从单轮对话中提取的信息

    Attributes:
        timestamp: 时间戳
        user_snippet: 用户消息摘要（前 100 字符）
        summary: 本轮对话摘要
        facts: 提取的关键事实
    """

    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] = field(default_factory=list)


@dataclass
class MemoryEntryInput:
    """简化的条目输入（用于 add_entry，facts 可选）

    Attributes:
        timestamp: 时间戳
        user_snippet: 用户消息摘要
        summary: 本轮对话摘要
        facts: 关键事实（可选）
    """

    timestamp: str
    user_snippet: str
    summary: str
    facts: list[str] | None = None


@dataclass
class FileMetadata:
    """文件元数据：记录上传文件的信息

    Attributes:
        name: 文件名
        path: 相对路径（相对于会话 files 目录）
        size: 文件大小（bytes）
        mime_type: MIME 类型
        type: 文件类型（'image' | 'text' | 'binary'）
        description: 图片描述或文本预览
        timestamp: 入站时间
        source: 来源（'cli' | 'feishu'）
    """

    name: str
    path: str
    size: int
    mime_type: str
    type: str  # 'image' | 'text' | 'binary'
    description: str = ""
    timestamp: str = ""
    source: str = "cli"  # 'cli' | 'feishu'


@dataclass
class SessionMemory:
    """会话记忆：持久化的跨会话记忆数据

    Attributes:
        session_id: 会话唯一标识
        cumulative_summary: 运行累计摘要
        key_facts: 关键事实列表
        ground_truth_facts: 可追溯、可纠正的长期确定事实
        entries: 历史条目列表
        uploaded_files: 上传的文件列表
        total_turns: 累计对话轮数
        first_seen: 首次活跃时间
        last_active: 最后活跃时间
        chat_id: 关联的聊天室 ID
        sender_id: 关联的发送者 ID
    """

    session_id: str
    cumulative_summary: str = ""
    key_facts: list[str] = field(default_factory=list)
    ground_truth_facts: list[GroundTruthFact] = field(default_factory=list)
    entries: list[MemoryEntry] = field(default_factory=list)
    uploaded_files: list[FileMetadata] = field(default_factory=list)
    total_turns: int = 0
    first_seen: str = ""
    last_active: str = ""
    chat_id: str | None = None
    sender_id: str | None = None


@runtime_checkable
class MemoryStoreProtocol(Protocol):
    """记忆存储接口协议

    负责会话记忆的加载、保存、更新和添加条目。

    该 Protocol 同时满足以下两处使用的需求：
    - ``miniagent.runtime.context.RuntimeContext.memory_store``
    - ``miniagent.types.protocols`` 中的运行时注入

    Methods:
        load: 加载会话记忆
        save: 保存会话记忆
        update_summary: 更新摘要和事实
        update_user_snippet: 更新用户消息摘要
        append_message: 追加消息到记忆
        add_entry: 添加记忆条目

    Attributes:
        _state_dir: 状态目录路径（可选属性，某些实现可能不提供）
    """

    _state_dir: str

    async def load(self, session_key: str) -> SessionMemory | None:
        """加载会话记忆

        Args:
            session_key: 会话唯一标识

        Returns:
            会话记忆对象，若不存在则返回 None
        """
        ...

    async def save(self, memory: SessionMemory) -> None:
        """保存会话记忆

        Args:
            memory: 会话记忆对象
        """
        ...

    async def update_summary(self, session_key: str, summary: str, facts: list[str]) -> None:
        """更新摘要和关键事实

        Args:
            session_key: 会话唯一标识
            summary: 运行累计摘要
            facts: 关键事实列表
        """
        ...

    async def update_user_snippet(self, session_key: str, snippet: str) -> None:
        """更新用户消息摘要

        Args:
            session_key: 会话唯一标识
            snippet: 用户消息摘要（前 100 字符）
        """
        ...

    async def append_message(self, session_key: str, role: str, content: str) -> None:
        """追加消息到记忆

        Args:
            session_key: 会话唯一标识
            role: 消息角色（user/assistant）
            content: 消息内容
        """
        ...

    async def add_entry(self, session_key: str, entry: MemoryEntryInput | dict[str, Any]) -> None:
        """添加记忆条目

        Args:
            session_key: 会话唯一标识
            entry: 记忆条目输入（实现类可将 dict 规范为 MemoryEntryInput）
        """
        ...

    async def add_file(self, session_key: str, file_meta: FileMetadata) -> None:
        """添加上传文件到记忆

        Args:
            session_key: 会话唯一标识
            file_meta: 文件元数据
        """
        ...


@dataclass
class SessionOptions:
    """会话配置选项

    Attributes:
        title: 会话标题（可重命名）
        description: 会话描述
        parent_session_id: 继承的父会话 ID
        workspace_path: 自定义工作空间路径
        allowed_tools: 初始工具白名单
        toolboxes: 初始工具箱列表
    """

    title: str = ""
    description: str | None = None
    parent_session_id: str | None = None
    workspace_path: str | None = None
    allowed_tools: list[str] | None = None
    toolboxes: list[Any] | None = None  # list[Toolbox]


@dataclass
class Session:
    """会话：独立的 Agent 执行上下文

    Attributes:
        id: 会话唯一 ID
        description: 会话描述
        created_at: 创建时间
        last_active_at: 最后活跃时间
        turn_count: 累计对话轮数
        workspace_path: 历史字段名；实际含义为 **工具文件沙箱根**（``…/sessions/<safe>/files``），与 ``files_path`` 属性相同
        config_overrides: 会话配置覆盖
        destroyed: 是否已销毁
        conversation_history: 对话历史（用于上下文保持）
    """

    id: str
    description: str = ""
    created_at: str = ""
    last_active_at: str = ""
    turn_count: int = 0
    workspace_path: str | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)
    destroyed: bool = False
    conversation_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def files_path(self) -> str | None:
        """工具可读写的文件目录（与 ``SessionManager`` 的 ``SessionConfig.files_path`` 一致）。"""
        return self.workspace_path


@runtime_checkable
class SessionManagerProtocol(Protocol):
    """会话管理器接口协议

    管理会话的创建、获取、列表、销毁、切换，以及工具升降维。

    该 Protocol 用于 ``miniagent.runtime.context.RuntimeContext`` 的
    session_manager 字段类型，支持依赖注入模式。
    """

    def get_or_create(self, id: str, options: SessionOptions | None = None) -> Session:
        """创建或获取会话

        Args:
            id: 会话唯一标识
            options: 会话配置选项

        Returns:
            会话对象
        """
        ...

    def get(self, id: str) -> Session | None:
        """获取会话

        Args:
            id: 会话唯一标识

        Returns:
            会话对象，若不存在则返回 None
        """
        ...

    def list(self) -> list[Session]:
        """列出所有活跃会话

        Returns:
            会话列表
        """
        ...

    def destroy(self, id: str) -> bool:
        """销毁会话

        Args:
            id: 会话唯一标识

        Returns:
            是否成功销毁
        """
        ...

    def get_active_id(self) -> str:
        """获取当前活跃会话 ID

        Returns:
            活跃会话 ID
        """
        ...

    def set_active(self, id: str) -> bool:
        """切换活跃会话

        Args:
            id: 目标会话 ID

        Returns:
            是否成功切换
        """
        ...

    def promote_tool(self, session_id: str, tool_name: str) -> bool:
        """工具升维（添加到会话工具白名单）

        Args:
            session_id: 会话 ID
            tool_name: 工具名称

        Returns:
            是否成功升维
        """
        ...

    def demote_tool(self, session_id: str, tool_name: str) -> bool:
        """工具降维（从会话工具白名单移除）

        Args:
            session_id: 会话 ID
            tool_name: 工具名称

        Returns:
            是否成功降维
        """
        ...


__all__ = [
    "GroundTruthFact",
    "MemoryEntry",
    "MemoryEntryInput",
    "FileMetadata",
    "SessionMemory",
    "MemoryStoreProtocol",
    "SessionOptions",
    "Session",
    "SessionManagerProtocol",
]
