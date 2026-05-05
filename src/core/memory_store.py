"""Mini Agent Python — 跨会话记忆持久化存储

管理每个会话（chatId/senderId）的长期记忆。

存储结构：
- state/memory/<sessionId>.json
- 每次对话结束后自动保存
- 下次对话启动时自动加载并注入到 system prompt

记忆内容：
- cumulative_summary: 累计对话摘要
- key_facts: 关键事实列表（偏好、约定、重要信息）
- entries: 最近对话条目
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from src.types.memory import MemoryEntry, MemoryEntryInput, MemoryStoreProtocol, SessionMemory


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

    从 SessionMemory 提取关键事实、累计摘要和最近对话条目，
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

    # 累计摘要
    if memory.cumulative_summary:
        parts.append("## 之前的对话摘要")
        parts.append(memory.cumulative_summary)

    # 最近条目
    if memory.entries:
        parts.append("## 最近的对话")
        for entry in memory.entries[-5:]:
            time_str = entry.timestamp[:16].replace("T", " ")
            parts.append(
                f"[{time_str}] 用户: {entry.user_snippet} → 摘要: {entry.summary}"
            )

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

    基于文件系统的 JSON 持久化，带内存缓存。

    Example:
        store = DefaultMemoryStore(state_dir="./state")
        memory = await store.load("session-1")
        await store.update_summary("session-1", "用户询问了天气", [])
    """

    def __init__(self, state_dir: str = "state") -> None:
        """创建记忆存储

        Args:
            state_dir: 状态存储目录
        """
        self._state_dir = state_dir
        self._memory_dir = os.path.join(state_dir, "memory")
        self._cache: dict[str, SessionMemory] = {}

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
            return self._cache[session_id]

        try:
            self._ensure_dir()
            file_path = self._file_path(session_id)
            if not os.path.exists(file_path):
                return None

            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            memory = SessionMemory(
                session_id=data["session_id"],
                cumulative_summary=data.get("cumulative_summary", ""),
                key_facts=data.get("key_facts", []),
                entries=[
                    MemoryEntry(**e) if isinstance(e, dict) else e
                    for e in data.get("entries", [])
                ],
                total_turns=data.get("total_turns", 0),
                first_seen=data.get("first_seen", ""),
                last_active=data.get("last_active", ""),
                chat_id=data.get("chat_id"),
                sender_id=data.get("sender_id"),
            )
            self._cache[session_id] = memory
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
                "total_turns": memory.total_turns,
                "first_seen": memory.first_seen,
                "last_active": memory.last_active,
                "chat_id": memory.chat_id,
                "sender_id": memory.sender_id,
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self._cache[memory.session_id] = memory
        except Exception as e:
            print(f"[memory-store] 保存失败 [{memory.session_id}]: {e}")

    async def update_summary(
        self, session_id: str, summary: str, facts: list[str]
    ) -> None:
        """更新摘要和事实

        Args:
            session_id: 会话唯一标识
            summary: 新的对话摘要
            facts: 关键事实列表
        """
        memory = await self.load(session_id)
        if not memory:
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
        for fact in facts:
            normalized = fact.lower().strip()
            exists = any(f.lower().strip() == normalized for f in memory.key_facts)
            if not exists:
                memory.key_facts.append(fact)
        if len(memory.key_facts) > 20:
            memory.key_facts = memory.key_facts[-20:]

        memory.last_active = datetime.now(timezone.utc).isoformat()
        await self.save(memory)

    async def add_entry(
        self, session_id: str, entry: MemoryEntryInput
    ) -> None:
        """添加对话条目

        Args:
            session_id: 会话唯一标识
            entry: 记忆条目输入
        """
        memory = await self.load(session_id)
        if not memory:
            return

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

        # Layer 3: 索引到关键词倒排索引（延迟导入，避免循环依赖）
        # from src.core.keyword_index import index_entry
        # index_entry(session_id, full_entry)


__all__ = [
    "DefaultMemoryStore",
    "format_memory_for_prompt",
    "extract_facts",
    "generate_turn_summary",
]
