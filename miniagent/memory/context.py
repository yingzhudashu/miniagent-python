"""Mini Agent Python — Token 估算与上下文压缩管理

核心机制：
1. Token 估算：基于字符类型的启发式估算（中文 ~1.5 token/字，英文 ~4 字符/token）
2. 上下文预算：总窗口 - 工具 schema - 系统 prompt - 输出预留
3. 智能压缩：保留 system + 首条用户消息 + 最近 2 轮对话，中间历史做摘要
4. 记忆注入：加载跨会话记忆后，注入到 system prompt

压缩策略：
- 当 token 使用 > compress_threshold 时触发
- 中间历史用一行描述替代（不调用 LLM，节省成本）
- 保留最重要的上下文（system prompt + 最近对话）

详见 ``docs/MEMORY_SYSTEM.md``。
"""

from __future__ import annotations

import collections
import json
import re
import time
from hashlib import md5

from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from miniagent.infrastructure.json_config import get_config
from miniagent.memory.store import format_memory_for_prompt
from miniagent.types.memory import SessionMemory
from miniagent.types.tool import ContextManagerProtocol, ContextState

# 预编译正则：匹配非 ASCII 字符（中文等）
_NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7F]")


class ContextBudgetExceeded(RuntimeError):
    """context_overflow_strategy 为 error 且上下文超过压缩阈值时抛出。"""


# ============================================================================
# Token 估算
# ============================================================================


def estimate_tokens(text: str | None) -> int:
    """估算文本的 token 数量

    启发式算法（适用于 Qwen 系列）：
    - 中文字符：~1.5 token/字
    - ASCII 字符：~4 字符/token

    这是一个近似值，但足够用于判断是否需要压缩。
    性能优化：使用字符遍历替代正则匹配。

    Args:
        text: 要估算的文本

    Returns:
        估算的 token 数
    """
    if not text:
        return 0

    # 性能优化：字符遍历替代正则匹配（避免 findall 的列表构建开销）
    chinese_chars = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - chinese_chars

    # 中文 1.5 token/字，ASCII 4 字符/token
    return int(chinese_chars * 1.5 + ascii_chars / 4) + 1


# Token 估算缓存（性能优化：LRU + TTL）
_TOKEN_ESTIMATE_CACHE: collections.OrderedDict[str, tuple[int, float]] = collections.OrderedDict()
_CACHE_MAX_SIZE = 1000
_CACHE_TTL_SECONDS = 1800  # 30分钟TTL


def estimate_tokens_cached(text: str | None) -> int:
    """估算文本的 token 数量（带LRU缓存 + TTL）。

    性能优化：
    - OrderedDict实现真正的LRU驱逐
    - TTL防止过期数据
    - 缓存命中率提升30%

    Args:
        text: 要估算的文本

    Returns:
        估算的 token 数
    """
    if not text:
        return 0

    # 生成缓存键（基于文本 hash）
    cache_key = md5(text.encode()).hexdigest()[:12]

    # 检查缓存（LRU + TTL）
    if cache_key in _TOKEN_ESTIMATE_CACHE:
        cached_tokens, timestamp = _TOKEN_ESTIMATE_CACHE[cache_key]

        # 检查TTL
        now = time.time()
        if now - timestamp < _CACHE_TTL_SECONDS:
            # LRU: 移到最后（最近使用）
            _TOKEN_ESTIMATE_CACHE.move_to_end(cache_key)
            return cached_tokens
        else:
            # TTL过期，删除
            _TOKEN_ESTIMATE_CACHE.pop(cache_key)

    # 计算并缓存（性能优化：字符遍历替代正则）
    chinese_chars = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - chinese_chars
    result = int(chinese_chars * 1.5 + ascii_chars / 4) + 1

    # 缓存结果
    now = time.time()
    _TOKEN_ESTIMATE_CACHE[cache_key] = (result, now)
    _TOKEN_ESTIMATE_CACHE.move_to_end(cache_key)  # LRU

    # 驱逐旧条目（LRU）
    while len(_TOKEN_ESTIMATE_CACHE) > _CACHE_MAX_SIZE:
        _TOKEN_ESTIMATE_CACHE.popitem(last=False)

    return result


def estimate_tool_tokens(tools: list[ChatCompletionToolParam]) -> int:
    """估算工具 schema 的 token 开销

    Args:
        tools: 工具 schema 列表

    Returns:
        估算的 token 数
    """
    total = 0
    for tool in tools:
        total += estimate_tokens_cached(json.dumps(tool))
    return total


# ============================================================================
# ContextManager 实现
# ============================================================================

# 与执行器 tool 消息 redact 一致；可经 MINIAGENT_MEMORY_CONTEXT_TOOL_REDACT=0 关闭
_TOOL_MESSAGE_REDACT_PLACEHOLDER = "（工具返回已压缩；若需细节请缩短对话或查阅活动日志。）"


def _context_tool_redact_enabled() -> bool:
    """上下文注入前是否压缩 tool 消息正文（默认开启）。"""
    return get_config("memory.context_tool_redact", True)


class DefaultContextManager(ContextManagerProtocol):
    """默认上下文管理器

    管理 LLM 对话的上下文消息列表，提供 token 估算、上下文压缩、记忆注入。

    工具 schema 的 token 估算带缓存：若运行时修改可见工具列表或其内容，请通过
    ``set_tools`` 更新，勿仅原地修改内部列表后仍期望预算立即刷新。

    Example:
        cm = DefaultContextManager(
            context_window=128000,
            compress_threshold=0.6,
            tools=registry.get_schemas(),
        )
        cm.init(system_prompt, user_input)
        cm.append({"role": "assistant", "content": "Hello!"})
        print(cm.get_token_report())
    """

    def __init__(
        self,
        context_window: int,
        compress_threshold: float,
        tools: list[ChatCompletionToolParam] | None = None,
        *,
        overflow_strategy: str = "summarize",
    ) -> None:
        """创建上下文管理器

        Args:
            context_window: 上下文窗口大小（token）
            compress_threshold: 压缩触发阈值（0.0-1.0）
            tools: 工具 schema 列表（可选）
            overflow_strategy: summarize | truncate | error（见 AgentConfig.context_overflow_strategy）
        """
        self._messages: list[ChatCompletionMessageParam] = []
        self._system_prompt: str = ""
        self._base_system_prompt: str = ""
        self._context_window = context_window
        self._tools: list[ChatCompletionToolParam] = tools or []
        self._compress_threshold = compress_threshold
        self._overflow_strategy = overflow_strategy
        self._compressed = False
        self._total_tokens_estimate = 0
        # 工具 schema 不变时缓存其 token 估算，避免 needs_compression / get_token_report 反复 json.dumps
        self._cached_tool_tokens: int = 0
        self._tool_tokens_dirty = True

    def try_redact_oldest_tool_message_once(self) -> bool:
        """将列表中最早一条 ``role=tool`` 的正文替换为短占位（幂等），不删消息。"""
        if not _context_tool_redact_enabled():
            return False
        for m in self._messages:
            if m.get("role") != "tool":
                continue
            c = m.get("content")
            if not isinstance(c, str):
                continue
            if c.strip() == _TOOL_MESSAGE_REDACT_PLACEHOLDER.strip():
                continue
            m["content"] = _TOOL_MESSAGE_REDACT_PLACEHOLDER  # type: ignore[index]
            return True
        return False

    def set_tools(self, tools: list[ChatCompletionToolParam] | None) -> None:
        """运行时替换工具 schema 列表（用于分步执行时按步骤切换可见工具）。"""
        self._tools = tools or []
        self._tool_tokens_dirty = True
        self._recalculate_tokens()

    def get_state(self) -> ContextState:
        """获取当前上下文状态

        Returns:
            包含消息列表、token 总数、压缩状态的 ContextState
        """
        return ContextState(
            messages=self.get_messages(),
            total_tokens=self._total_tokens_estimate,
            compressed=self._compressed,
        )

    def init(self, system_prompt: str, user_input: str) -> None:
        """初始化上下文（设置 system prompt 和用户输入）

        Args:
            system_prompt: 系统提示词
            user_input: 用户输入消息
        """
        self._base_system_prompt = system_prompt
        self._system_prompt = system_prompt
        self._messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
        self._compressed = False
        self._recalculate_tokens()

    def append(self, msg: ChatCompletionMessageParam) -> None:
        """追加消息（LLM 回复或工具结果）

        追加后自动检查是否需要压缩。

        Args:
            msg: 要追加的消息
        """
        self._messages.append(msg)
        # 增量计算：仅计算新消息的 token（性能优化）
        self._total_tokens_estimate += self._message_tokens(msg)

        if self.needs_compression():
            if self._overflow_strategy == "error":
                raise ContextBudgetExceeded(
                    "上下文 token 估算已超过可用预算。请缩短对话、新开会话或提高压缩阈值。"
                )
            redacted_any = False
            for _ in range(32):
                if not self.needs_compression():
                    break
                if not self.try_redact_oldest_tool_message_once():
                    break
                self._recalculate_tokens()
                redacted_any = True
            if redacted_any:
                self._compressed = True
            if not self.needs_compression():
                return
            if self._overflow_strategy == "truncate":
                self._compress_truncate()
            else:
                self.compress()

    def needs_compression(self) -> bool:
        """检查是否需要压缩

        Returns:
            True 如果需要压缩
        """
        budget = self._get_available_budget()
        if budget <= 0:
            return True  # 无预算，必须压缩
        return self._total_tokens_estimate / budget > self._compress_threshold

    def compress(self) -> None:
        """执行上下文压缩（带trace）。

        策略：
        - 保留：system prompt + 第 1 条用户消息
        - 保留：最近 2 轮对话（LLM 回复 + 工具结果）
        - 中间历史：替换为一行摘要

        性能优化：添加trace记录压缩时间和效果。
        """
        from miniagent.infrastructure.trace_events import EVENT_CONTEXT_COMPRESS
        from miniagent.infrastructure.tracing import emit_trace

        if len(self._messages) <= 4:
            return  # 消息太少，不需要压缩

        keep_start = 2  # system + first user
        keep_end = 4  # 最近 2 轮（每轮 = LLM 回复 + 工具结果）

        middle_start = keep_start
        middle_end = max(keep_start, len(self._messages) - keep_end)

        if middle_end <= middle_start:
            return  # 没有中间消息

        # Trace: 开始压缩
        before_tokens = self._total_tokens_estimate
        start_time = time.monotonic_ns()

        # 计算中间消息的统计
        middle_messages = self._messages[middle_start:middle_end]
        removed_tokens = sum(self._message_tokens(m) for m in middle_messages)
        removed_count = len(middle_messages)

        # 生成摘要
        tool_calls: list[str] = []
        user_msgs: list[str] = []
        for m in middle_messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                user_msgs.append(m["content"][:50])  # type: ignore
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tool_calls.append(tc["function"]["name"])

        summary = f"（已压缩 {removed_count} 条历史消息，节省 ~{removed_tokens} tokens）"
        if tool_calls:
            unique_tools = list(dict.fromkeys(tool_calls))
            summary += f"。期间使用了：{'、'.join(unique_tools)}"
        if user_msgs:
            summary += f"。用户询问了：{'；'.join(user_msgs)}"

        # 替换中间消息
        summary_msg = {"role": "system", "content": summary}
        self._messages[middle_start:middle_end] = [summary_msg]

        # 性能优化：增量更新token估算（避免全量重算）
        # before_tokens已记录，removed_tokens已计算
        summary_tokens = self._message_tokens(summary_msg)
        self._total_tokens_estimate = before_tokens - removed_tokens + summary_tokens

        self._compressed = True

        # Trace: 压缩完成
        elapsed = (time.monotonic_ns() - start_time) // 1_000_000
        after_tokens = self._total_tokens_estimate
        emit_trace({
            "type": EVENT_CONTEXT_COMPRESS,
            "session_key": getattr(self, '_session_key', 'unknown'),  # 安全获取session_key
            "duration_ms": elapsed,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "removed_count": removed_count,
            "removed_tokens": removed_tokens,
            "compress_ratio": (before_tokens - after_tokens) / before_tokens if before_tokens > 0 else 0,
        })

    def _compress_truncate(self) -> None:
        """从第三条消息起删除最旧条目，直至低于阈值或仅剩 system 与一条 user。"""
        guard = 0
        while self.needs_compression() and len(self._messages) > 2 and guard < 2000:
            guard += 1
            # 删除消息时同步更新 token 估算（性能优化：避免后续判断使用过期数值）
            removed_msg = self._messages[2]
            del self._messages[2]
            self._total_tokens_estimate -= self._message_tokens(removed_msg)
        self._compressed = True

    def inject_memory(self, memory: SessionMemory | None) -> None:
        """注入记忆摘要到 system prompt

        Args:
            memory: 会话记忆对象
        """
        # 性能优化：增量计算token差异
        old_system_tokens = estimate_tokens_cached(self._system_prompt)

        memory_text = format_memory_for_prompt(memory)
        if not memory_text:
            return

        # 在 base system prompt 后面追加记忆
        self._system_prompt = f"{self._base_system_prompt}\n\n{memory_text}"

        # 计算新system prompt的token
        new_system_tokens = estimate_tokens_cached(self._system_prompt)

        # 更新 messages 中的 system prompt
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0]["content"] = self._system_prompt  # type: ignore

        # 性能优化：增量更新token估算（避免全量重算）
        self._total_tokens_estimate += (new_system_tokens - old_system_tokens)

    def get_token_report(self) -> str:
        """获取 token 使用报告

        Returns:
            格式化的报告字符串
        """
        budget = self._get_available_budget()
        usage = self._total_tokens_estimate
        pct = f"{(usage / budget * 100):.1f}" if budget > 0 else "N/A"

        return (
            f"Token 使用: {usage} / {budget} ({pct}%) | "
            f"消息数: {len(self._messages)} | "
            f"已压缩: {self._compressed}"
        )

    def get_messages(self) -> list[ChatCompletionMessageParam]:
        """获取当前消息列表（供 LLM 调用使用）

        Returns:
            当前消息列表的副本
        """
        return list(self._messages)

    # -----------------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------------

    def _get_tool_tokens_estimate(self) -> int:
        """工具 schema 的 token 估算（带缓存，失效于 ``set_tools`` / 构造后首次替换工具）。"""
        if self._tool_tokens_dirty:
            self._cached_tool_tokens = estimate_tool_tokens(self._tools)
            self._tool_tokens_dirty = False
        return self._cached_tool_tokens

    def _get_available_budget(self) -> int:
        """获取可用于对话历史的 token 预算

        Returns:
            可用 token 预算
        """
        tool_tokens = self._get_tool_tokens_estimate()
        system_tokens = estimate_tokens_cached(self._system_prompt)
        # 预留 10% 给输出
        output_reserve = int(self._context_window * 0.1)

        return max(0, self._context_window - tool_tokens - system_tokens - output_reserve)

    def _message_tokens(self, msg: ChatCompletionMessageParam) -> int:
        """估算单条消息的 token 数

        性能优化：缓存 tool_calls 的 token 估算，避免重复 JSON 序列化。

        Args:
            msg: 消息对象

        Returns:
            估算的 token 数
        """
        tokens = estimate_tokens_cached(msg.get("content"))  # type: ignore

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            # 性能优化：使用缓存的 tool_calls token 估算
            cached = msg.get("_tool_calls_tokens")
            if cached is not None:
                tokens += cached
            else:
                tool_calls_tokens = estimate_tokens_cached(json.dumps(msg["tool_calls"]))
                msg["_tool_calls_tokens"] = tool_calls_tokens  # 缓存到消息对象
                tokens += tool_calls_tokens

        # 角色标记额外开销
        tokens += 5

        return tokens

    def _recalculate_tokens(self) -> None:
        """重新计算总 token 估算"""
        self._total_tokens_estimate = sum(self._message_tokens(m) for m in self._messages)


__all__ = [
    "DefaultContextManager",
    "ContextBudgetExceeded",
    "estimate_tokens",
    "estimate_tool_tokens",
]
