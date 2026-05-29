"""Mini Agent Python — 跨会话记忆持久化存储

管理每个会话（chatId/senderId）的长期记忆。

存储结构：
- workspaces/memory/<sessionId>.json
- 每次对话结束后自动保存
- 下次对话启动时自动加载并注入到 system prompt

记忆内容：
- cumulative_summary: 累计对话摘要
- key_facts: 关键事实列表（偏好、约定、重要信息）
- entries: 最近对话条目

详见 ``docs/MEMORY_SYSTEM.md``（会话级 Layer 2）。
"""

from __future__ import annotations

import collections
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.types.memory import FileMetadata, MemoryEntry, MemoryEntryInput, MemoryStoreProtocol, SessionMemory

_logger = get_logger(__name__)


# ============================================================================
# 路径配置
# ============================================================================


def _memory_file_path(state_dir: str, session_id: str) -> str:
    """生成记忆文件路径

    文件名安全处理：将非法字符替换为下划线。

    Args:
        state_dir: 状态存储目录
        session_id: 会话唯一标识

    Returns:
        记忆文件的完整路径
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    return os.path.join(state_dir, "memory", f"{safe}.json")


# ============================================================================
# 记忆格式化
# ============================================================================


def format_memory_for_prompt(memory: SessionMemory | None) -> str:
    """将记忆格式化为可注入 system prompt 的文本

    从 SessionMemory 提取关键事实、上传文件、累计摘要和最近对话条目，
    格式化为 Markdown 文本，可直接拼接到 system prompt 中。

    Args:
        memory: 会话记忆对象（None 时返回空字符串）

    Returns:
        格式化后的记忆文本（或空字符串）

    Example:
        memory_text = format_memory_for_prompt(session_memory)
        system_prompt += f"\n\n{memory_text}"
    """
    if not memory:
        return ""

    parts: list[str] = []

    # 关键事实（最重要的信息）
    if memory.key_facts:
        parts.append("## 关键记忆")
        for fact in memory.key_facts[-10:]:
            parts.append(f"- {fact}")

    # 上传的文件（新增）
    if memory.uploaded_files:
        parts.append("## 上传的文件")
        for file in memory.uploaded_files[-10:]:
            type_label = {"image": "图片", "text": "文本", "binary": "文件"}.get(file.type, "文件")
            size_kb = file.size // 1024 if file.size >= 1024 else file.size
            size_label = f"{size_kb}KB" if file.size >= 1024 else f"{size_kb}B"
            parts.append(f"- {file.name} ({type_label}, {size_label})")
            if file.description:
                # 描述截断到 200 字符
                desc = file.description[:200] + "…" if len(file.description) > 200 else file.description
                parts.append(f"  内容: {desc}")

    # 累计摘要
    if memory.cumulative_summary:
        parts.append("## 之前的对话摘要")
        parts.append(memory.cumulative_summary)

    # 最近条目
    if memory.entries:
        parts.append("## 最近的对话")
        for entry in memory.entries[-5:]:
            time_str = entry.timestamp[:16].replace("T", " ")
            parts.append(f"[{time_str}] 用户: {entry.user_snippet} → 摘要: {entry.summary}")

    if not parts:
        return ""

    return "【历史记忆】\n\n" + "\n\n".join(parts) + "\n\n【记忆结束】"


def extract_facts(text: str) -> list[str]:
    """从对话中提取关键事实（简单启发式）

    识别包含记忆性关键词（"记住"、"以后"、"偏好"、"默认"、"喜欢"等）的句子，
    提取其内容作为关键事实存储。

    Args:
        text: 要分析的对话文本

    Returns:
        提取的关键事实列表

    Example:
        extract_facts('记住我喜欢用中文回复，以后默认用 Markdown 格式')
        # → ['我喜欢用中文回复', '默认用 Markdown 格式']
    """
    facts: list[str] = []

    # 匹配模式：包含记忆性关键词的句子
    patterns = [
        r"记住[：:，,。]\s*(.+)",
        r"以后[都]?[要]?[：:，,。]\s*(.+)",
        r"偏好[是]?[：:，,。]\s*(.+)",
        r"默认[是]?[：:，,。]\s*(.+)",
        r"不[要喜欢]([^.。]+)",
        r"喜[欢好]([^.。]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match and match.group(1):
            fact = match.group(1).strip()[:200]
            if len(fact) > 2:
                facts.append(fact)

    return facts


def generate_turn_summary(
    user_message: str,
    tool_calls: list[dict[str, Any]],
    final_reply: str,
) -> str:
    """生成单轮对话摘要（简单版，不调用 LLM）

    从用户消息、工具调用和最终回复中提取关键信息，
    拼接为简短的中文摘要字符串。

    Args:
        user_message: 用户原始消息
        tool_calls: 本轮使用的工具调用列表
        final_reply: LLM 的最终回复

    Returns:
        摘要字符串

    Example:
        generate_turn_summary(
            "帮我创建 README.md",
            [{"name": "write_file", "args": {"path": "README.md"}}],
            "已创建 README.md 文件"
        )
        # → "用户帮我创建 README.md，使用了 write_file，回复: 已创建 README.md 文件"
    """
    parts: list[str] = []

    # 用户意图（取前 50 字符）
    intent = user_message.strip()[:50]
    if intent:
        parts.append(f"用户{intent}")

    # 工具使用
    if tool_calls:
        tools = ", ".join(tc.get("name", "") for tc in tool_calls)
        parts.append(f"使用了 {tools}")

    # 结果摘要
    if final_reply:
        summary = final_reply.strip()[:100]
        if summary:
            parts.append(f"回复: {summary}")

    return "，".join(parts)


# ============================================================================
# 记忆存储实现
# ============================================================================


class DefaultMemoryStore(MemoryStoreProtocol):
    """默认记忆存储实现

    基于文件系统的 JSON 持久化，带 LRU 内存缓存。
    缓存上限由 ``MINIAGENT_MEMORY_STORE_CACHE_MAX`` 控制（默认 50）。

    Example:
        store = DefaultMemoryStore(state_dir="./workspaces")
        memory = await store.load("session-1")
        await store.update_summary("session-1", "用户询问了天气", [])
    """

    def __init__(
        self,
        state_dir: str = "workspaces",
        *,
        keyword_index: Any | None = None,
    ) -> None:
        """创建记忆存储

        Args:
            state_dir: 状态存储目录
            keyword_index: 关键词索引实例（写入条目时更新；未提供则用模块默认索引）
        """
        self._state_dir = state_dir
        self._memory_dir = os.path.join(state_dir, "memory")
        self._cache: collections.OrderedDict[str, SessionMemory] = collections.OrderedDict()
        self._cache_max = int(os.environ.get("MINIAGENT_MEMORY_STORE_CACHE_MAX", "50"))
        self._keyword_index = keyword_index

    def _cache_put(self, session_id: str, memory: SessionMemory) -> None:
        """将记忆放入 LRU 缓存，超过上限时驱逐最旧条目。"""
        self._cache[session_id] = memory
        self._cache.move_to_end(session_id)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def flush_keyword_index(self) -> None:
        """将 Layer 3 关键词索引的挂起变更写入磁盘。"""
        try:
            idx = self._keyword_index
            if idx is None:
                from miniagent.memory.defaults import get_process_default_memory_bundle

                idx = get_process_default_memory_bundle()[2]
            idx.save()
        except Exception:
            pass

    def _ensure_dir(self) -> None:
        """确保记忆目录存在"""
        os.makedirs(self._memory_dir, exist_ok=True)

    def _file_path(self, session_id: str) -> str:
        """获取记忆文件路径

        Args:
            session_id: 会话唯一标识

        Returns:
            记忆文件的完整路径
        """
        return _memory_file_path(self._state_dir, session_id)

    async def load(self, session_id: str) -> SessionMemory | None:
        """加载会话记忆

        先查缓存，未命中则从磁盘读取。

        Args:
            session_id: 会话唯一标识

        Returns:
            会话记忆对象，不存在返回 None
        """
        # 先查缓存
        if session_id in self._cache:
            self._cache.move_to_end(session_id)
            return self._cache[session_id]

        try:
            self._ensure_dir()
            file_path = self._file_path(session_id)
            if not os.path.exists(file_path):
                return None

            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)

            entries: list[MemoryEntry] = []
            for e in data.get("entries", []):
                if isinstance(e, MemoryEntry):
                    entries.append(e)
                elif isinstance(e, dict):
                    try:
                        entries.append(
                            MemoryEntry(
                                timestamp=str(e.get("timestamp", "")),
                                user_snippet=str(e.get("user_snippet", "")),
                                summary=str(e.get("summary", "")),
                                facts=list(e.get("facts") or []),
                            )
                        )
                    except Exception:
                        continue

            # 加载上传的文件
            uploaded_files: list[FileMetadata] = []
            for f in data.get("uploaded_files", []):
                try:
                    uploaded_files.append(
                        FileMetadata(
                            name=str(f.get("name", "")),
                            path=str(f.get("path", "")),
                            size=int(f.get("size", 0)),
                            mime_type=str(f.get("mime_type", "")),
                            type=str(f.get("type", "binary")),
                            description=str(f.get("description", "")),
                            timestamp=str(f.get("timestamp", "")),
                            source=str(f.get("source", "cli")),
                        )
                    )
                except Exception:
                    continue

            memory = SessionMemory(
                session_id=data["session_id"],
                cumulative_summary=data.get("cumulative_summary", ""),
                key_facts=data.get("key_facts", []),
                entries=entries,
                uploaded_files=uploaded_files,
                total_turns=data.get("total_turns", 0),
                first_seen=data.get("first_seen", ""),
                last_active=data.get("last_active", ""),
                chat_id=data.get("chat_id"),
                sender_id=data.get("sender_id"),
            )
            self._cache_put(session_id, memory)
            return memory

        except Exception:
            return None

    async def save(self, memory: SessionMemory) -> None:
        """保存会话记忆到磁盘

        Args:
            memory: 会话记忆对象
        """
        try:
            self._ensure_dir()
            file_path = self._file_path(memory.session_id)

            data = {
                "session_id": memory.session_id,
                "cumulative_summary": memory.cumulative_summary,
                "key_facts": memory.key_facts,
                "entries": [
                    {
                        "timestamp": e.timestamp,
                        "user_snippet": e.user_snippet,
                        "summary": e.summary,
                        "facts": e.facts,
                    }
                    for e in memory.entries
                ],
                "uploaded_files": [
                    {
                        "name": f.name,
                        "path": f.path,
                        "size": f.size,
                        "mime_type": f.mime_type,
                        "type": f.type,
                        "description": f.description,
                        "timestamp": f.timestamp,
                        "source": f.source,
                    }
                    for f in memory.uploaded_files
                ],
                "total_turns": memory.total_turns,
                "first_seen": memory.first_seen,
                "last_active": memory.last_active,
                "chat_id": memory.chat_id,
                "sender_id": memory.sender_id,
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self._cache_put(memory.session_id, memory)
        except Exception as e:
            _logger.error("保存失败 [%s]: %s", memory.session_id, e)

    async def update_summary(self, session_id: str, summary: str, facts: list[str]) -> None:
        """更新摘要和事实

        Args:
            session_id: 会话唯一标识
            summary: 新的对话摘要
            facts: 关键事实列表
        """
        memory = await self.load(session_id)
        if not memory:
            # 自动创建新会话记忆
            now = datetime.now(timezone.utc).isoformat()
            memory = SessionMemory(
                session_id=session_id,
                cumulative_summary=summary[-2000:] if summary else "",
                key_facts=list(facts),
                total_turns=0,
                first_seen=now,
                last_active=now,
            )
            await self.save(memory)
            return

        # 更新累计摘要（保留最近 2000 字符）
        if summary:
            new_summary = (
                f"{memory.cumulative_summary}\n- {summary}"
                if memory.cumulative_summary
                else summary
            )
            memory.cumulative_summary = new_summary[-2000:]

        # 更新关键事实（去重，最多保留 20 条）
        existing = {f.lower().strip() for f in memory.key_facts}
        for fact in facts:
            normalized = fact.lower().strip()
            if normalized not in existing:
                memory.key_facts.append(fact)
                existing.add(normalized)
        if len(memory.key_facts) > 20:
            memory.key_facts = memory.key_facts[-20:]

        memory.last_active = datetime.now(timezone.utc).isoformat()
        await self.save(memory)

    async def add_entry(self, session_id: str, entry: MemoryEntryInput | dict[str, Any]) -> None:
        """添加对话条目

        Args:
            session_id: 会话唯一标识
            entry: 记忆条目输入（或兼容的 dict，与 executor 传入格式一致）
        """
        if isinstance(entry, dict):
            entry = MemoryEntryInput(
                timestamp=str(entry.get("timestamp", "")),
                user_snippet=str(entry.get("user_snippet", "")),
                summary=str(entry.get("summary", "")),
                facts=list(entry.get("facts") or []) if entry.get("facts") is not None else None,
            )

        memory = await self.load(session_id)
        if not memory:
            # 自动创建新会话记忆
            now = datetime.now(timezone.utc).isoformat()
            memory = SessionMemory(
                session_id=session_id,
                total_turns=0,
                first_seen=now,
                last_active=now,
            )

        full_entry = MemoryEntry(
            timestamp=entry.timestamp,
            user_snippet=entry.user_snippet,
            summary=entry.summary,
            facts=entry.facts or [],
        )
        memory.entries.append(full_entry)
        memory.total_turns += 1

        # 只保留最近 20 条
        if len(memory.entries) > 20:
            memory.entries = memory.entries[-20:]

        memory.last_active = datetime.now(timezone.utc).isoformat()
        await self.save(memory)

        # Layer 3: 索引到关键词倒排索引（与进程默认 bundle 同源）
        try:
            idx = self._keyword_index
            if idx is None:
                from miniagent.memory.defaults import get_process_default_memory_bundle

                idx = get_process_default_memory_bundle()[2]
            idx.index_entry(session_id, full_entry)
        except Exception:
            pass  # 关键词索引失败不影响主流程

        # Layer 3b: 嵌入索引（异步，失败静默回退）
        try:
            from miniagent.memory.embedding_search import (
                embedding_search_enabled,
                get_embed_provider,
            )

            if embedding_search_enabled():
                provider = get_embed_provider(state_dir=self._state_dir)
                text = " ".join([entry.user_snippet, entry.summary, *(entry.facts or [])])
                emb = await provider.get_embedding(text)
                if emb is not None:
                    provider.index.index_entry(session_id, full_entry, embedding=emb)
        except Exception:
            pass  # 嵌入索引失败不影响主流程

    async def add_file(self, session_id: str, file_meta: FileMetadata) -> None:
        """添加上传文件到记忆

        Args:
            session_id: 会话唯一标识
            file_meta: 文件元数据
        """
        memory = await self.load(session_id)
        if not memory:
            now = datetime.now(timezone.utc).isoformat()
            memory = SessionMemory(
                session_id=session_id,
                total_turns=0,
                first_seen=now,
                last_active=now,
            )

        memory.uploaded_files.append(file_meta)

        # 最多保留 50 个文件记录
        if len(memory.uploaded_files) > 50:
            memory.uploaded_files = memory.uploaded_files[-50:]

        # 图片或有描述的文件，同时作为 key_fact
        if file_meta.description:
            type_label = {"image": "图片", "text": "文本文件", "binary": "文件"}.get(file_meta.type, "文件")
            fact = f"用户上传过{type_label} {file_meta.name}: {file_meta.description[:100]}"
            existing = {f.lower().strip() for f in memory.key_facts}
            if fact.lower().strip() not in existing and len(memory.key_facts) < 20:
                memory.key_facts.append(fact)

        memory.last_active = datetime.now(timezone.utc).isoformat()
        await self.save(memory)


__all__ = [
    "DefaultMemoryStore",
    "format_memory_for_prompt",
    "extract_facts",
    "generate_turn_summary",
    "add_file_to_memory",
]


async def add_file_to_memory(session_id: str, file_meta: FileMetadata, store: Any = None) -> None:
    """将文件添加到会话记忆（便捷函数）

    Args:
        session_id: 会话 ID
        file_meta: 文件元数据
        store: 记忆存储实例（None 时使用进程默认）
    """
    if store is None:
        from miniagent.memory.defaults import get_process_default_memory_bundle

        store = get_process_default_memory_bundle()[0]

    if store is None:
        return

    await store.add_file(session_id, file_meta)
