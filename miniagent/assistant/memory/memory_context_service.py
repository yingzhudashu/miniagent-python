"""记忆上下文 Protocol 的默认实现。

将 ``miniagent.agent.types.memory_context`` 中定义的接口落地为可注入服务，
供 ``ApplicationContainer``、``execute_plan`` 与测试使用。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

from miniagent.agent.history import format_history_for_llm
from miniagent.agent.ports.runtime import KeywordIndexProtocol
from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.memory import MemoryEntryInput, MemoryStoreProtocol
from miniagent.agent.types.memory_context import (
    MemoryInjectionResult,
    MemorySearchProtocol,
)
from miniagent.assistant.engine.bg_session_cleanup import is_background_session_key
from miniagent.assistant.memory.embedding_search import (
    EmbeddingSearchProvider,
    embedding_search_enabled,
)
from miniagent.assistant.memory.keyword_index import (
    KeywordIndex,
    format_search_results,
    search_relevant_with_index,
)
from miniagent.assistant.memory.store import (
    extract_facts,
    format_memory_for_prompt,
    generate_turn_summary,
)


def _is_ephemeral_session(session_key: str | None) -> bool:
    return is_background_session_key(session_key or "")


class DefaultMemorySearch:
    """关键词 + 嵌入检索的默认 ``MemorySearchProtocol`` 实现。"""

    def __init__(
        self,
        keyword_index: KeywordIndexProtocol,
        memory_store: MemoryStoreProtocol | None = None,
        embedding_provider: EmbeddingSearchProvider | None = None,
    ) -> None:
        """绑定关键词索引与可选记忆存储（嵌入检索需 store 的 state_dir）。"""
        self._keyword_index = keyword_index
        self._memory_store = memory_store
        self._embedding_provider = embedding_provider

    async def search_relevant_memory(
        self,
        query: str,
        session_key: str,
        *,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """并行关键词与嵌入检索，合并去重后返回。"""
        del session_key  # 主路径跨会话检索；保留参数供替代实现按会话过滤
        if not query.strip():
            return []

        embed_task: asyncio.Task[Any] | None = None
        provider = self._embedding_provider
        if embedding_search_enabled() and provider is not None:
            try:
                embed_task = asyncio.create_task(
                    provider.search(query, limit=top_k, min_score=0.3)
                )
            except Exception:
                embed_task = None

        kw_task = asyncio.create_task(
            asyncio.to_thread(
                search_relevant_with_index,
                cast(KeywordIndex, self._keyword_index),
                query,
                top_k,
                0,
            )
        )

        relevant: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        if embed_task is not None:
            embed_results, kw_results = await asyncio.gather(
                embed_task, kw_task, return_exceptions=True
            )
            if not isinstance(embed_results, Exception) and provider is not None:
                for row in provider.expand_results(embed_results):
                    key = (row["session_id"], row["timestamp"])
                    if key not in seen:
                        relevant.append(row)
                        seen.add(key)
            if not isinstance(kw_results, Exception):
                for kw in kw_results:
                    key = (kw["session_id"], kw["timestamp"])
                    if key not in seen and len(relevant) < top_k:
                        relevant.append(kw)
                        seen.add(key)
        else:
            kw_results = await kw_task
            relevant = list(kw_results)

        return relevant[:top_k]

    def format_search_results(
        self,
        results: list[dict[str, Any]],
        *,
        max_length: int | None = None,
    ) -> str:
        """委托 ``keyword_index.format_search_results`` 格式化检索条目。"""
        return format_search_results(results, max_length=max_length)


class DefaultMemoryHistory:
    """会话历史加载与 LLM 格式化的默认 ``MemoryHistoryProtocol`` 实现。"""

    def __init__(self, session_manager: Any | None = None) -> None:
        """可选绑定 ``SessionManager``，用于 ``load_history`` 读磁盘历史。"""
        self._session_manager = session_manager

    async def load_history(
        self,
        session_key: str,
        *,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        """从 ``SessionManager`` 加载会话历史，可选保留最近 ``max_messages`` 条。"""
        history: list[dict[str, Any]] = []
        if self._session_manager is not None:
            loader = getattr(self._session_manager, "load_session_history_async", None)
            if callable(loader):
                history = await loader(session_key)
            else:
                sync_loader = getattr(self._session_manager, "load_session_history", None)
                if callable(sync_loader):
                    history = await asyncio.to_thread(sync_loader, session_key)
        if max_messages is not None and max_messages > 0:
            history = history[-max_messages:]
        return history

    def format_history_for_llm(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """委托 ``history_bridge.format_history_for_llm`` 清洗角色并按 token 预算裁剪。"""
        return format_history_for_llm(messages, max_tokens=max_tokens)


class DefaultMemoryContext:
    """默认 ``MemoryContextProtocol``：检索上下文注入与回合后持久化。"""

    def __init__(
        self,
        memory_store: MemoryStoreProtocol,
        keyword_index: KeywordIndexProtocol,
        *,
        memory_search: MemorySearchProtocol | None = None,
        embedding_provider: EmbeddingSearchProvider | None = None,
    ) -> None:
        """绑定记忆存储、关键词索引与可选自定义检索实现。"""
        self._memory_store = memory_store
        self._keyword_index = keyword_index
        self._memory_search = memory_search or DefaultMemorySearch(
            keyword_index, memory_store, embedding_provider
        )
        self._embedding_provider = embedding_provider

    async def inject_memory_to_messages(
        self,
        messages: list[dict],
        session_key: str,
        agent_config: AgentConfig,
        *,
        tool_registry: Any | None = None,
        user_input: str | None = None,
        activity_log: Any | None = None,
        keyword_index: KeywordIndexProtocol | None = None,
    ) -> tuple[list[dict], dict[str, Any]]:
        """构建本轮记忆上下文元数据；消息列表保持不变（cache-friendly 主路径）。"""
        del tool_registry, activity_log  # 保留签名供替代实现使用
        _ = agent_config

        memory_context_str: str | None = None
        keyword_context_str: str | None = None
        relevant: list[dict[str, Any]] = []

        if session_key and not _is_ephemeral_session(session_key):
            memory = await self._memory_store.load(session_key)
            memory_text = format_memory_for_prompt(memory)
            if memory_text:
                memory_context_str = memory_text

            query = (user_input or "").strip()
            if query:
                search = self._memory_search
                if keyword_index is not None and keyword_index is not self._keyword_index:
                    search = DefaultMemorySearch(
                        keyword_index,
                        self._memory_store,
                        self._embedding_provider,
                    )
                relevant = await search.search_relevant_memory(
                    query, session_key, top_k=8
                )
                search_text = search.format_search_results(relevant)
                if search_text:
                    keyword_context_str = search_text

        turn_keyword_context = "\n\n".join(
            p for p in (memory_context_str, keyword_context_str) if p and p.strip()
        ) or None

        metadata: dict[str, Any] = {
            "memory_context": memory_context_str,
            "keyword_context": keyword_context_str,
            "turn_keyword_context": turn_keyword_context,
            "relevant": relevant,
            "relevant_count": len(relevant),
        }
        return messages, metadata

    async def inject_memory(
        self,
        messages: list[dict],
        session_key: str,
        agent_config: AgentConfig,
        *,
        user_input: str | None = None,
        keyword_index: KeywordIndexProtocol | None = None,
    ) -> MemoryInjectionResult:
        """``MemoryInjectionResult`` 包装，便于结构化消费注入结果。"""
        out_messages, metadata = await self.inject_memory_to_messages(
            messages,
            session_key,
            agent_config,
            user_input=user_input,
            keyword_index=keyword_index,
        )
        return MemoryInjectionResult(
            messages=out_messages,
            memory_metadata=metadata,
        )

    async def save_memory_after_turn(
        self,
        session_key: str,
        user_input: str,
        reply: str,
        memory_store: Any,
        *,
        tool_calls: list[dict] | None = None,
        token_usage: dict | None = None,
    ) -> None:
        """保存回合记忆：事实提取、摘要生成、条目写入与索引刷新。"""
        del token_usage  # 预留供活动日志或统计扩展
        if _is_ephemeral_session(session_key):
            return

        store = memory_store or self._memory_store
        calls = tool_calls or []
        tool_results_text = " ".join(
            str(call.get("result", ""))
            for call in calls
            if isinstance(call, dict)
        )
        facts = extract_facts(
            " ".join(part for part in (user_input, reply, tool_results_text) if part)
        )
        summary = generate_turn_summary(user_input, calls, reply)
        now = datetime.now(timezone.utc).isoformat()
        entry = MemoryEntryInput(
            timestamp=now,
            user_snippet=user_input[:100],
            summary=summary,
            facts=facts,
        )
        record_turn = getattr(store, "record_turn", None)
        if callable(record_turn):
            await record_turn(session_key, summary, facts, entry)
        else:
            # Compatibility for injected third-party stores implementing only
            # the stable MemoryStoreProtocol surface.
            await store.update_summary(session_key, summary, facts)
            await store.add_entry(session_key, entry)
        flush_ki_async = getattr(store, "flush_keyword_index_async", None)
        if callable(flush_ki_async):
            await flush_ki_async()
        else:
            flush_ki = getattr(store, "flush_keyword_index", None)
            if callable(flush_ki):
                flush_ki()


def create_default_memory_context(
    memory_store: MemoryStoreProtocol,
    keyword_index: KeywordIndexProtocol,
    *,
    embedding_provider: EmbeddingSearchProvider | None = None,
) -> DefaultMemoryContext:
    """由记忆存储与关键词索引构造默认记忆上下文服务。"""
    return DefaultMemoryContext(
        memory_store=memory_store,
        keyword_index=keyword_index,
        embedding_provider=embedding_provider,
    )


__all__ = [
    "DefaultMemoryContext",
    "DefaultMemorySearch",
    "DefaultMemoryHistory",
    "create_default_memory_context",
]
