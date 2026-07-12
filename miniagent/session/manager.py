"""Mini Agent Python — 多会话管理器

每个会话拥有独立的工作空间、工具注册表、技能、记忆。
会话间默认完全隔离，除非显式"升维"才共享到主空间。

工作空间结构：
    workspaces/
    ├── sessions/
    │   └── <sessionId>/
    │       ├── files/        — 会话文件（工具操作默认目录）
    │       ├── skills/       — 会话级技能
    │       ├── history_snapshots/ — 编号历史快照
    │       └── config.json   — 会话配置
    ├── memory/
    │   ├── <sessionId>.json
    │   └── keyword-index.json
    └── instances/            — 多实例注册表
        └── <instanceId>/
            ├── meta.json
            └── heartbeat

设计背景见 ``docs/ARCHITECTURE.md``（会话与记忆）；长期记忆文件布局见 ``docs/MEMORY_SYSTEM.md``。

**与引擎的衔接**：进程内在 ``miniagent.engine.init.init_subsystems`` 中构造默认实现；``UnifiedEngine.run_agent_with_thinking`` 按 ``session_key`` 解析 ``files/`` 根目录、会话级工具注册表与历史落盘路径，勿在业务层绕过 ``SessionManager`` 直接写 ``workspaces/sessions/<id>`` 以免与锁、索引不一致。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.atomic_json import atomic_dump_json
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.config import normalize_conversation_history
from miniagent.types.memory import Session, SessionOptions
from miniagent.types.skill import Skill
from miniagent.types.tool import Toolbox, ToolContext, ToolDefinition
from miniagent.utils.session_id import safe_session_id

_logger = get_logger(__name__)

# ─── 会话历史硬限制（性能优化：防止内存膨胀）──

MAX_HISTORY_MESSAGES = get_config("memory.max_history_messages", 200)


def _truncate_history(
    history: list[dict[str, Any]], max_messages: int = MAX_HISTORY_MESSAGES
) -> list[dict[str, Any]]:
    """截断历史消息，保留 system + 首条用户 + 最后 N-2 条消息。"""
    if len(history) <= max_messages:
        return history
    # 保留 system 消息（通常是第一条）
    system_msgs = [m for m in history if m.get("role") == "system"]
    other_msgs = [m for m in history if m.get("role") != "system"]
    if len(system_msgs) > 0 and len(other_msgs) > max_messages - 1:
        # 保留首条用户消息 + 最后剩余消息
        first_user = next((m for m in other_msgs if m.get("role") == "user"), None)
        remaining = other_msgs[-(max_messages - len(system_msgs) - (1 if first_user else 0)) :]
        return system_msgs + ([first_user] if first_user else []) + remaining
    # 简单截断：保留最后 max_messages 条
    return history[-max_messages:]


def _get_history(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """以 Session.conversation_history 为真相源，并确保 ctx 引用一致。"""
    session: Session = ctx["session"]
    history = session.conversation_history
    if ctx.get("conversation_history") is not history:
        ctx["conversation_history"] = history
    return history


def _set_history(ctx: dict[str, Any], history: list[dict[str, Any]]) -> None:
    """同步 Session 与 ctx 的 conversation_history 引用。"""
    ctx["session"].conversation_history = history
    ctx["conversation_history"] = history


def _load_history_json_file(path: str) -> list[dict[str, Any]]:
    """从 ``history.json`` 路径读取、规范化并截断历史（不修改会话内存）。"""
    if not os.path.isfile(path):
        return []
    try:
        file_size = os.path.getsize(path)
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
        history = normalize_conversation_history(raw)
        original_count = len(history)
        max_msgs = int(get_config("memory.max_history_messages", MAX_HISTORY_MESSAGES))
        history = _truncate_history(history, max_messages=max_msgs)
        if original_count > len(history):
            _logger.info(
                "history.json 已截断加载: %s (%d → %d 条)",
                path,
                original_count,
                len(history),
            )
        elif file_size > 5 * 1024 * 1024:
            _logger.info(
                "history.json 较大 (%d MB)，已加载最近 %d 条: %s",
                file_size // (1024 * 1024),
                len(history),
                path,
            )
        return history
    except json.JSONDecodeError as e:
        _logger.warning("history.json JSON 格式无效，将使用空历史: %s → %s", path, e)
    except OSError as e:
        _logger.warning("history.json 读取失败，将使用空历史: %s → %s", path, e)
    except Exception as e:
        _logger.warning("history.json 加载失败，将使用空历史: %s → %s", path, e)
    return []


def _load_history_from_disk(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """从磁盘读取 history.json（不修改内存）。"""
    path = os.path.join(ctx["config"].workspace_path, "history.json")
    return _load_history_json_file(path)


# ============================================================================
# 会话列表字段（list_all_sessions_with_info 返回 id/number）
# ============================================================================


def session_info_id(entry: dict) -> str:
    """从 ``list_all_sessions_with_info`` 条目解析会话 ID。"""
    return str(entry.get("id") or "")


def session_info_number(entry: dict) -> int:
    """从 ``list_all_sessions_with_info`` 条目解析会话编号。"""
    n = entry.get("number", 0)
    try:
        return int(n)
    except (TypeError, ValueError):
        return 0


# ============================================================================
# 路径
# ============================================================================


def _get_state_dir() -> str:
    """获取状态目录"""
    from miniagent.infrastructure.paths import resolve_state_dir

    return resolve_state_dir()


def _get_workspaces_dir() -> str:
    """获取工作空间目录

    返回 workspaces/sessions/ 目录，会话数据存储在 workspaces/sessions/<sessionId>/
    """
    return os.path.join(_get_state_dir(), "sessions")


# ============================================================================
# 会话配置
# ============================================================================


@dataclass
class SessionConfig:
    """会话配置

    Attributes:
        session_id: 会话 ID
        session_number: 会话编号（用于显示，如 #1, #2）
        workspace_path: 工作空间路径
        files_path: 文件目录（工具操作默认位置）
        skills_path: 技能目录
        created_at: 创建时间
        last_active: 最后活跃时间
        title: 会话标题（可重命名）
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
    session_number: int = 0
    title: str = ""
    description: str = ""
    chat_id: str | None = None
    sender_id: str | None = None


@dataclass(frozen=True, slots=True)
class _DiskSessionConfig:
    """Compact metadata needed by session discovery commands."""

    dir_name: str
    workspace_path: str
    session_id: str
    session_number: int
    title: str
    created_at: str
    last_active: str


@dataclass(frozen=True, slots=True)
class _DiskConfigCacheEntry:
    """One fingerprinted parse result; ``config=None`` caches invalid JSON."""

    mtime_ns: int
    size: int
    config: _DiskSessionConfig | None


@dataclass
class SessionInfo:
    """会话信息（用于列表展示）

    包含会话 ID、描述、时间戳、工具/技能数量等摘要信息，
    用于 CLI 和飞书的会话列表展示。

    Attributes:
        session_id: 会话唯一标识
        description: 会话描述
        created_at: 创建时间（ISO 8601 格式）
        last_active: 最后活跃时间（ISO 8601 格式）
        tool_count: 注册的工具数量
        skill_count: 注册的技能数量
        files_path: 文件目录路径
    """

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


class DefaultSessionManager:
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
        *,
        clawhub: Any | None = None,
        max_sessions: int | None = None,  # 性能优化：内存中最多保持的会话数
    ) -> None:
        """创建会话管理器

        Args:
            main_registry: 主空间工具注册表
            main_toolboxes: 主空间工具箱列表
            main_skills: 主空间技能列表
            clawhub: ClawHub 客户端，注入到 :meth:`get_tool_context` 供技能类工具使用
            max_sessions: 内存中最多保持的会话数，默认 50，超过时 LRU 驎出
        """
        if max_sessions is None:
            from miniagent.core.constants import SESSION_MANAGER_MAX_SESSIONS

            max_sessions = SESSION_MANAGER_MAX_SESSIONS
        # 性能优化：使用 OrderedDict 实现 LRU 驎出
        from collections import OrderedDict

        self._sessions: OrderedDict[str, dict] = OrderedDict()  # sessionId -> context (LRU)
        self._max_sessions: int = max_sessions
        self._main_registry = main_registry
        self._main_toolboxes: list[Toolbox] = main_toolboxes or []
        self._main_skills: list[Skill] = main_skills or []
        self._clawhub = clawhub
        self._active_session_id: str | None = None
        self._next_number: int = 1  # 下一个会话编号
        self._session_locks: dict[str, threading.RLock] = {}
        self._session_lock_users: dict[str, int] = {}
        self._session_locks_meta = threading.Lock()
        from miniagent.core.constants import SESSION_CONFIG_CACHE_MAX_SIZE

        self._disk_config_cache: dict[str, _DiskConfigCacheEntry] = {}
        self._disk_config_cache_max = SESSION_CONFIG_CACHE_MAX_SIZE
        self._disk_config_cache_lock = threading.Lock()
        self._ensure_workspaces_dir()
        self._scan_existing_numbers()

    @contextmanager
    def _session_guard(self, session_id: str) -> Iterator[None]:
        """Acquire one reference-counted per-session lock.

        Idle locks for sessions no longer resident in the LRU are removed on
        exit. Counting users before acquisition prevents a waiter from racing
        with cleanup and receiving a second lock for the same session.
        """
        with self._session_locks_meta:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[session_id] = lock
            self._session_lock_users[session_id] = self._session_lock_users.get(session_id, 0) + 1
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._session_locks_meta:
                users = self._session_lock_users.get(session_id, 1) - 1
                if users > 0:
                    self._session_lock_users[session_id] = users
                else:
                    self._session_lock_users.pop(session_id, None)
                    if session_id not in self._sessions:
                        self._session_locks.pop(session_id, None)

    def _discard_session_lock_if_idle(self, session_id: str) -> None:
        """Drop a non-resident lock unless another thread is using or waiting on it."""
        with self._session_locks_meta:
            if self._session_lock_users.get(session_id, 0) == 0:
                self._session_locks.pop(session_id, None)

    def _ensure_workspaces_dir(self) -> None:
        """确保工作空间目录存在"""
        os.makedirs(_get_workspaces_dir(), exist_ok=True)

    def _evict_oldest_if_needed(self) -> None:
        """性能优化：LRU 驎出最旧的会话，保持内存使用在 max_sessions 限制内。

        驎出策略：
        - 当内存中会话数超过 max_sessions 时，保存并移除最旧的会话
        - 不影响活跃会话（active_session_id 不被驱逐）
        - 驎出前保存会话历史到磁盘
        """
        while len(self._sessions) > self._max_sessions:
            # 获取最旧的会话（OrderedDict 的第一个）
            oldest_id = next(iter(self._sessions))
            # 不驱逐活跃会话
            if oldest_id == self._active_session_id:
                # 如果最旧的是活跃会话，跳过它，找下一个
                if len(self._sessions) > 1:
                    # 移动活跃会话到末尾
                    self._sessions.move_to_end(oldest_id)
                    oldest_id = next(iter(self._sessions))
                else:
                    break  # 只有一个活跃会话，不驱逐
            # 保存历史到磁盘
            self.save_session_history(oldest_id)
            # 移除内存中的会话
            del self._sessions[oldest_id]
            self._discard_session_lock_if_idle(oldest_id)
            _logger.debug("LRU 驎出会话: %s (内存中剩余 %d)", oldest_id, len(self._sessions))

    def _touch_session(self, session_id: str) -> None:
        """性能优化：将指定会话移动到 OrderedDict 末尾（标记为最近使用）。

        Args:
            session_id: 会话 ID
        """
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)

    def _clone_core_tools(self) -> tuple[DefaultToolRegistry, int]:
        """克隆主空间核心工具到新注册表。

        核心工具 = 不属于任何 toolbox 的工具（基础能力）。

        Returns:
            (新注册表, 克隆数量)
        """
        registry = DefaultToolRegistry()
        core_count = 0
        for name, tool in self._main_registry.get_all().items():
            if not tool.toolbox:  # 无 toolbox = 核心工具
                try:
                    registry.register(
                        name,
                        ToolDefinition(
                            schema=tool.schema,
                            handler=tool.handler,
                            permission=tool.permission,
                            help_text=tool.help_text,
                            toolbox=tool.toolbox,
                        ),
                    )
                    core_count += 1
                except ValueError as e:
                    _logger.debug("工具已存在，跳过: %s", e)
        return registry, core_count

    def _scan_disk_configs(self) -> list[_DiskSessionConfig]:
        """统一磁盘扫描：读取所有会话 config.json。

        替代之前 3 处重复的磁盘扫描逻辑：
        - _scan_existing_numbers()
        - _scan_disk_sessions()
        - list_all_sessions_with_info() 的磁盘部分

        Returns:
            紧凑且只读的会话元数据；未变化文件复用指纹缓存。
        """
        workspaces = _get_workspaces_dir()
        result: list[_DiskSessionConfig] = []
        seen_paths: set[str] = set()
        with self._disk_config_cache_lock:
            try:
                entries = os.scandir(workspaces)
            except OSError:
                return result

            with entries:
                for entry in entries:
                    try:
                        if not entry.is_dir():
                            continue
                    except OSError:
                        continue

                    config_path = os.path.join(entry.path, "config.json")
                    try:
                        stat = os.stat(config_path)
                    except OSError:
                        continue
                    seen_paths.add(config_path)

                    cached = self._disk_config_cache.get(config_path)
                    if (
                        cached is not None
                        and cached.mtime_ns == stat.st_mtime_ns
                        and cached.size == stat.st_size
                    ):
                        if cached.config is not None:
                            result.append(cached.config)
                        continue

                    parsed: _DiskSessionConfig | None = None
                    try:
                        with open(config_path, encoding="utf-8-sig") as f:
                            raw = json.load(f)
                        if not isinstance(raw, dict):
                            raise ValueError("config root must be an object")
                        number = raw.get("session_number", 0)
                        parsed = _DiskSessionConfig(
                            dir_name=entry.name,
                            workspace_path=entry.path,
                            session_id=str(raw.get("session_id") or ""),
                            session_number=number if isinstance(number, int) else 0,
                            title=str(raw.get("title") or ""),
                            created_at=str(raw.get("created_at") or ""),
                            last_active=str(raw.get("last_active") or ""),
                        )
                    except Exception as e:
                        _logger.debug("扫描磁盘配置失败: %s", e)

                    if (
                        cached is not None
                        or len(self._disk_config_cache) < self._disk_config_cache_max
                    ):
                        self._disk_config_cache[config_path] = _DiskConfigCacheEntry(
                            mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size,
                            config=parsed,
                        )
                    if parsed is not None:
                        result.append(parsed)

            stale_paths = self._disk_config_cache.keys() - seen_paths
            for path in stale_paths:
                self._disk_config_cache.pop(path, None)
        return result

    def _scan_existing_numbers(self) -> None:
        """扫描已有会话编号，确定下一个可用编号。"""
        max_num = 0
        for entry in self._scan_disk_configs():
            if entry.session_number > max_num:
                max_num = entry.session_number
        self._next_number = max_num + 1

    def _make_safe_id(self, session_id: str) -> str:
        """将非法路径字符替换为安全字符。

        使用统一的 safe_session_id 函数，确保与其他模块一致。

        Args:
            session_id: 原始会话 ID

        Returns:
            安全的会话 ID
        """
        return safe_session_id(session_id)

    def _save_config(self, config: SessionConfig) -> None:
        """保存会话配置到磁盘

        Args:
            config: 会话配置
        """
        try:
            config_path = os.path.join(config.workspace_path, "config.json")
            atomic_dump_json(
                config_path,
                {
                    "session_id": config.session_id,
                    "workspace_path": config.workspace_path,
                    "files_path": config.files_path,
                    "skills_path": config.skills_path,
                    "created_at": config.created_at,
                    "last_active": config.last_active,
                    "session_number": config.session_number,
                    "title": config.title,
                    "description": config.description,
                    "chat_id": config.chat_id,
                    "sender_id": config.sender_id,
                },
                indent=2,
                ensure_ascii=False,
            )
            try:
                stat = os.stat(config_path)
            except OSError:
                with self._disk_config_cache_lock:
                    self._disk_config_cache.pop(config_path, None)
            else:
                cached_config = _DiskSessionConfig(
                    dir_name=os.path.basename(config.workspace_path),
                    workspace_path=config.workspace_path,
                    session_id=config.session_id,
                    session_number=config.session_number,
                    title=config.title,
                    created_at=config.created_at,
                    last_active=config.last_active,
                )
                with self._disk_config_cache_lock:
                    if (
                        config_path in self._disk_config_cache
                        or len(self._disk_config_cache) < self._disk_config_cache_max
                    ):
                        self._disk_config_cache[config_path] = _DiskConfigCacheEntry(
                            mtime_ns=stat.st_mtime_ns,
                            size=stat.st_size,
                            config=cached_config,
                        )
        except Exception:
            _logger.exception("会话配置保存失败: %s", config.workspace_path)

    # -----------------------------------------------------------------------
    # 会话历史持久化（Persistence Layer）
    # -----------------------------------------------------------------------
    #
    # 历史持久化机制：
    #   每个会话的对话历史保存在工作空间下的 history.json 文件中。
    #   格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    #
    # 保存时机：
    #   - CLI 输入：每次 agent turn 完成后调用 save_session_history()
    #   - 飞书通道消息：每条消息处理完成后调用 save_session_history()
    #   - run_agent_with_thinking() 内部对上述路径统一触发保存
    #
    # 加载时机：
    #   - _restore() 中自动加载：当检测到已有工作空间配置时，恢复历史
    #   - load_session_history() 显式加载：桥接模式启动时手动加载
    #
    # 存储路径：
    #   state/workspaces/<safe_session_id>/history.json
    # -----------------------------------------------------------------------

    def save_session_history(self, session_id: str) -> None:
        """持久化会话历史到磁盘

        将内存中的 conversation_history 写入工作空间的 history.json 文件。
        此方法在每次 agent turn 后调用，确保历史不会因重启丢失。

        Args:
            session_id: 会话 ID

        Note:
            静默失败（try/except pass），不影响主流程。
            历史持久化是增强功能，不是关键路径。
        """
        with self._session_guard(session_id):
            ctx = self._sessions.get(session_id)
            if not ctx:
                return
            try:
                history = _get_history(ctx)
                # 截断历史防止内存膨胀
                history = _truncate_history(history)
                _set_history(ctx, history)
                history_snapshot = [dict(message) for message in history]
                path = os.path.join(ctx["config"].workspace_path, "history.json")
                atomic_dump_json(
                    path,
                    history_snapshot,
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as e:
                _logger.warning("保存会话历史失败 (session=%s): %s", session_id, e)

    def load_session_history(self, session_id: str) -> list:
        """从磁盘加载会话历史

        读取工作空间中的 history.json，返回解析后的消息列表。
        用于桥接模式启动时恢复历史上下文。

        Args:
            session_id: 会话 ID

        Returns:
            历史消息列表，格式：[{"role": "user", "content": "..."}, ...]
            如果文件不存在或解析失败，返回空列表。
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return []
        return _load_history_from_disk(ctx)

    async def save_session_history_async(self, session_id: str) -> None:
        """异步持久化会话历史到磁盘（性能优化：不阻塞事件循环）。

        大历史文件写入可能耗时数十毫秒，使用 asyncio.to_thread 包装，
        避免 LLM 流式处理被阻塞。

        Args:
            session_id: 会话 ID

        Note:
            异步版本，在异步上下文中使用，不阻塞主事件循环。
        """
        await asyncio.to_thread(self.save_session_history, session_id)

    async def load_session_history_async(self, session_id: str) -> list:
        """异步从磁盘加载会话历史（性能优化：不阻塞事件循环）。

        Args:
            session_id: 会话 ID

        Returns:
            历史消息列表，如果文件不存在或解析失败，返回空列表。
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return []

        return await asyncio.to_thread(_load_history_from_disk, ctx)

    def load_session_history_range(
        self,
        session_id: str,
        start_idx: int = 0,
        count: int = 10,
    ) -> tuple[list, int]:
        """分批加载会话历史（从末尾向前计数）。

        用于 CLI 历史记录渐进式显示，避免一次性加载大量历史。

        Args:
            session_id: 会话 ID
            start_idx: 起始索引（从末尾计数，0 表示最新）
            count: 加载数量

        Returns:
            (消息列表, 总消息数)
            消息按时间顺序返回（从旧到新）。
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return [], 0

        history = _get_history(ctx)
        if len(history) == 0:
            disk_history = _load_history_from_disk(ctx)
            if disk_history:
                _set_history(ctx, disk_history)
                history = disk_history

        total = len(history)
        if total == 0:
            return [], 0

        # 从末尾切片（start_idx=0 表示最新）
        actual_start = max(0, total - start_idx - count)
        actual_end = total - start_idx

        messages = history[actual_start:actual_end]

        # 确保对话轮次完整：如果第一条是 assistant（缺少对应的 user），
        # 则向前扩展到包含其 user 消息（如果存在）
        if messages and messages[0].get("role") == "assistant":
            # 查找前一条 user 消息
            if actual_start > 0 and history[actual_start - 1].get("role") == "user":
                messages = [history[actual_start - 1]] + messages

        return messages, total

    def load_all_sessions(self) -> list[str]:
        """从磁盘加载所有已保存的会话 ID

        扫描工作空间目录，查找所有包含 config.json 的子目录，
        返回已持久化的会话 ID 列表。用于会话列表展示和恢复。

        Returns:
            已保存的会话 ID 列表（磁盘目录名，即 safe_id）
        """
        workspaces = _get_workspaces_dir()
        ids: list[str] = []
        try:
            entries = os.scandir(workspaces)
        except OSError:
            return ids
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "config.json")):
                        ids.append(entry.name)
                except OSError:
                    continue
        return ids

    def get_or_create(self, id: str, options: SessionOptions | None = None) -> Session:
        """获取或创建会话

        - 已存在 → 返回现有会话
        - 不存在 → 检查工作空间配置是否存在 → 加载历史 + 创建

        Args:
            id: 会话唯一标识
            options: 可选配置

        Returns:
            会话对象
        """
        with self._session_guard(id):
            if id in self._sessions:
                ctx = self._sessions[id]
                # 性能优化：标记为最近使用
                self._touch_session(id)
                ctx["config"].last_active = datetime.now(timezone.utc).isoformat()
                return ctx["session"]

            # 检查是否有持久化的工作空间（重启后恢复）
            safe_id = self._make_safe_id(id)
            workspace_path = os.path.join(_get_workspaces_dir(), safe_id)
            config_path = os.path.join(workspace_path, "config.json")
            if os.path.isfile(config_path):
                return self._restore(id, workspace_path, options)

            return self._create(id, options)

    def _build_session_ctx(
        self,
        session_id: str,
        config: SessionConfig,
        conversation_history: list | None = None,
        toolboxes: list | None = None,
    ) -> dict:
        """创建会话级上下文（注册表 + Session + ctx 字典）。

        抽取 _create() 和 _restore() 的公共逻辑：
        - 克隆核心工具
        - 构建 Session 对象
        - 注册到 _sessions 字典

        Args:
            session_id: 会话唯一标识
            config: 会话配置
            conversation_history: 对话历史（None 表示空）
            toolboxes: 工具箱列表

        Returns:
            会话上下文字典
        """
        registry, core_count = self._clone_core_tools()

        session = Session(
            id=session_id,
            description=config.description,
            created_at=config.created_at,
            last_active_at=config.last_active,
            workspace_path=config.files_path,
        )

        # 使用同一个 list 对象，确保 Session 和 ctx 引用一致
        # engine.py 通过 session.conversation_history 修改，
        # save_session_history 通过 ctx["conversation_history"] 保存
        # 如果两边不是同一个对象，保存的永远是空 list
        history = conversation_history if conversation_history is not None else []
        session.conversation_history = history

        ctx = {
            "session_id": session_id,
            "config": config,
            "registry": registry,
            "session": session,
            "toolboxes": toolboxes or [],
            "skills": [],
            "conversation_history": history,
        }
        self._sessions[session_id] = ctx
        # 性能优化：检查是否需要 LRU 驎出
        self._evict_oldest_if_needed()
        return ctx, core_count

    def _restore(
        self, session_id: str, workspace_path: str, options: SessionOptions | None
    ) -> Session:
        """从磁盘恢复已有会话（含历史）

        当检测到工作空间中已存在 config.json 时调用此方法。
        典型场景：应用重启后，恢复之前创建的会话。

        恢复流程：
        1. 读取 config.json，重建 SessionConfig
        2. 加载 history.json（如果存在）
        3. 调用 _build_session_ctx 统一构建上下文

        Args:
            session_id: 会话唯一标识
            workspace_path: 工作空间路径
            options: 可选配置（恢复时通常不使用）

        Returns:
            恢复后的 Session 对象
        """
        # 1. 读取配置
        config_path = os.path.join(workspace_path, "config.json")
        try:
            with open(config_path, encoding="utf-8-sig") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"会话配置 {config_path} JSON 格式无效: {e}") from e

        config = SessionConfig(
            session_id=raw["session_id"],
            workspace_path=raw["workspace_path"],
            files_path=raw["files_path"],
            skills_path=raw["skills_path"],
            created_at=raw["created_at"],
            last_active=datetime.now(timezone.utc).isoformat(),
            session_number=raw.get("session_number", 0),
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            chat_id=raw.get("chat_id"),
            sender_id=raw.get("sender_id"),
        )

        # 2. 加载历史（截断至 max_history_messages，避免大文件拖慢启动）
        history_path = os.path.join(workspace_path, "history.json")
        conversation_history = _load_history_json_file(history_path)

        # 3. 统一构建上下文
        ctx, core_count = self._build_session_ctx(session_id, config, conversation_history)

        _logger.info(
            "会话已恢复: %s (%d 个核心工具, %d 条历史)",
            session_id,
            core_count,
            len(conversation_history),
        )
        return ctx["session"]

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
            session_number=self._next_number,
            title=options.title if options and options.title else "",
            description=options.description if options else "",
        )
        self._next_number += 1

        self._save_config(config)

        toolboxes = options.toolboxes if options else []
        ctx, core_count = self._build_session_ctx(session_id, config, toolboxes=toolboxes)

        _logger.info("会话已创建: %s (%d 个核心工具)", session_id, core_count)
        return ctx["session"]

    def get(self, session_id: str) -> Session | None:
        """获取会话

        Args:
            session_id: 会话 ID

        Returns:
            会话对象，不存在返回 None
        """
        ctx = self._sessions.get(session_id)
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
        with self._session_guard(id):
            ctx = self._sessions.get(id)
            if not ctx:
                return False

            # Persist metadata only when the workspace survives. Writing a
            # config immediately before recursively deleting it adds avoidable
            # disk I/O to ephemeral/background session cleanup.
            if keep_files:
                ctx["config"].last_active = datetime.now(timezone.utc).isoformat()
                self._save_config(ctx["config"])
            del self._sessions[id]

        if not keep_files:
            try:
                import shutil

                shutil.rmtree(ctx["config"].workspace_path, ignore_errors=True)
            except Exception as e:
                _logger.debug("删除会话文件失败: %s", e)

        _logger.info("会话已销毁: %s", id)
        return True

    def forget_session(self, id: str) -> bool:
        """Remove one in-memory session without performing filesystem I/O.

        Background cleanup uses this on the event-loop thread and deletes the
        workspace separately in a worker.  This keeps the ``OrderedDict``
        owned by its normal thread while avoiding a recursive delete there.
        """
        with self._session_guard(id):
            if id not in self._sessions:
                return False
            del self._sessions[id]
            if self._active_session_id == id:
                self._active_session_id = None
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

    def rename_session(self, session_id: str, new_title: str) -> bool:
        """重命名会话标题

        如果会话在内存中不存在，会尝试从磁盘恢复后再重命名。

        Args:
            session_id: 会话 ID
            new_title: 新标题

        Returns:
            成功返回 True
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            # 尝试从磁盘恢复
            safe_id = self._make_safe_id(session_id)
            workspace_path = os.path.join(_get_workspaces_dir(), safe_id)
            config_path = os.path.join(workspace_path, "config.json")
            if os.path.isfile(config_path):
                self._restore(session_id, workspace_path, None)
                ctx = self._sessions.get(session_id)
            if not ctx:
                return False
        ctx["config"].title = new_title
        ctx["config"].last_active = datetime.now(timezone.utc).isoformat()
        self._save_config(ctx["config"])
        if ctx["session"].id == session_id:
            ctx["session"].description = new_title
        return True

    def get_session_by_number(self, number: int) -> dict | None:
        """通过会话编号查找会话（仅内存）。

        Args:
            number: 会话编号（如 1, 2, 3）

        Returns:
            会话上下文字典，不存在返回 None
        """
        for ctx in self._sessions.values():
            if ctx["config"].session_number == number:
                return ctx
        return None

    def _scan_disk_sessions(self) -> dict[int, str]:
        """扫描磁盘上所有已保存的会话，返回 {session_number: session_id}。

        用于在会话未加载到内存时，仍然能通过编号找到它们。
        """
        result = {}
        for entry in self._scan_disk_configs():
            if entry.session_number > 0 and entry.session_id:
                result[entry.session_number] = entry.session_id
        return result

    def resolve_session_id(self, id_or_number: str) -> str | None:
        """解析用户输入的会话标识。

        支持两种格式：
        - 纯数字：按 session_number 查找（如 "1" → "default"）
        - 字符串：直接使用作为 session_id

        先查内存，再查磁盘。即使会话尚未加载到内存，也能通过编号找到。

        Args:
            id_or_number: 用户输入

        Returns:
            解析后的 session_id，找不到返回 None
        """
        # 纯数字：按编号查找
        if id_or_number.isdigit():
            num = int(id_or_number)
            # 先查内存
            ctx = self.get_session_by_number(num)
            if ctx:
                return ctx["session_id"]
            # 再查磁盘
            disk_map = self._scan_disk_sessions()
            if num in disk_map:
                return disk_map[num]
            return None
        # 直接作为 session_id：先查内存，再查磁盘
        if id_or_number in self._sessions:
            return id_or_number
        # 磁盘上是否存在
        disk_map = self._scan_disk_sessions()
        for sid in disk_map.values():
            if sid == id_or_number:
                return sid
        return None

    def get_session_display_name(self, session_id: str) -> str:
        """获取会话显示名称（编号 + 标题）

        Args:
            session_id: 会话 ID

        Returns:
            显示名称，如 "#1 工作" 或 "#2 cli-interactive"
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return session_id
        config = ctx["config"]
        title = config.title if config.title else session_id
        return f"#{config.session_number} {title}"

    def list_all_sessions_with_info(self) -> list[dict]:
        """列出所有会话及其详细信息

        同时包含内存中和磁盘上已持久化的会话。

        Returns:
            会话信息列表
        """
        result = []
        seen_ids = set()

        # 先添加内存中的会话
        for ctx in self._sessions.values():
            config = ctx["config"]
            history = ctx.get("conversation_history", [])
            lock_owner = _get_session_lock_owner(config.workspace_path)
            result.append(
                {
                    "id": config.session_id,
                    "number": config.session_number,
                    "title": config.title or config.session_id,
                    "created_at": config.created_at,
                    "last_active": config.last_active,
                    "turn_count": len(history) // 2,
                    "locked": lock_owner is not None,
                    "lock_pid": lock_owner,
                }
            )
            seen_ids.add(config.session_id)

        # 再添加磁盘上存在但内存中没有的会话
        for entry in self._scan_disk_configs():
            sid = entry.session_id
            if sid in seen_ids:
                continue
            try:
                lock_owner = _get_session_lock_owner(entry.workspace_path)
                result.append(
                    {
                        "id": sid,
                        "number": entry.session_number,
                        "title": entry.title or sid,
                        "created_at": entry.created_at,
                        "last_active": entry.last_active,
                        "turn_count": 0,  # 不加载历史，避免开销
                        "locked": lock_owner is not None,
                        "lock_pid": lock_owner,
                    }
                )
            except Exception as e:
                _logger.debug("扫描会话信息失败: %s", e)

        return sorted(result, key=lambda x: x["number"])

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
            self._main_registry.register(
                tool_name,
                ToolDefinition(
                    schema=tool.schema,
                    handler=tool.handler,
                    permission=tool.permission,
                    help_text=tool.help_text,
                    toolbox=tool.toolbox,
                ),
            )
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

    def get_session_files_path(self, session_id: str) -> str | None:
        """返回会话文件沙箱根目录（``…/sessions/<safe_id>/files``）。

        仅在会话已加载到内存（例如刚 ``get_or_create``）后可用；否则返回 ``None``。
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            return None
        fp = getattr(ctx["config"], "files_path", "") or ""
        return fp if fp else None

    def get_tool_context(self, session_id: str) -> ToolContext:
        """获取会话的工具执行上下文

        Args:
            session_id: 会话 ID

        Returns:
            工具执行上下文，包含 cwd、allowed_paths、permission
        """
        ctx = self._sessions.get(session_id)
        if not ctx:
            default_workspace = get_config("paths.workspace", os.getcwd())
            return ToolContext(
                cwd=default_workspace,
                allowed_paths=[default_workspace],
                permission="allowlist",
                clawhub=self._clawhub,
                session_key=session_id,
            )

        return ToolContext(
            cwd=ctx["config"].files_path,
            allowed_paths=[ctx["config"].files_path],
            permission="allowlist",
            clawhub=self._clawhub,
            session_key=session_id,
        )

    # -----------------------------------------------------------------------
    # 会话级工具管理
    # -----------------------------------------------------------------------

    def register_tool(self, session_id: str, name: str, tool: ToolDefinition) -> bool:
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

    def refresh_main_skills(
        self,
        skills: list[Skill],
        toolboxes: list[Toolbox] | None = None,
    ) -> None:
        """热更新主空间技能与工具箱快照（``refresh_skills`` 后调用）。"""
        self._main_skills = list(skills)
        if toolboxes is not None:
            self._main_toolboxes = list(toolboxes)

    def get_main_registry(self) -> DefaultToolRegistry:
        """获取主空间工具注册表

        Returns:
            主空间的 ToolRegistry 实例
        """
        return self._main_registry


def _get_session_lock_owner(workspace_path: str) -> int | None:
    """获取会话的实例锁 PID（如果有）"""
    lock_file = os.path.join(workspace_path, ".lock")
    if os.path.isfile(lock_file):
        try:
            with open(lock_file) as f:
                return int(f.read().strip())
        except Exception as e:
            _logger.debug("读取锁文件失败: %s", e)
    return None


__all__ = [
    "DefaultSessionManager",
    "SessionConfig",
    "SessionInfo",
    "session_info_id",
    "session_info_number",
]
