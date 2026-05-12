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

import json
import os

from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from miniagent.memory.store import format_memory_for_prompt
from miniagent.types.memory import SessionMemory
from miniagent.types.tool import ContextManagerProtocol, ContextState, TokenEstimate


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

    Args:
        text: 要估算的文本

    Returns:
        估算的 token 数
    """
    if not text:
        return 0

    chinese_chars = 0
    ascii_chars = 0

    for ch in text:
        if ord(ch) > 127:
            chinese_chars += 1
        else:
            ascii_chars += 1

    # 中文 1.5 token/字，ASCII 4 字符/token
    return int(chinese_chars * 1.5 + ascii_chars / 4) + 1


def estimate_tool_tokens(tools: list[ChatCompletionToolParam]) -> int:
    """估算工具 schema 的 token 开销

    Args:
        tools: 工具 schema 列表

    Returns:
        估算的 token 数
    """
    total = 0
    for tool in tools:
        total += estimate_tokens(json.dumps(tool))
    return total


# ============================================================================
# ContextManager 实现
# ============================================================================

# 与执行器 tool 消息 redact 一致；可经 MINI_AGENT_CONTEXT_TOOL_REDACT=0 关闭
_TOOL_MESSAGE_REDACT_PLACEHOLDER = "（工具返回已压缩；若需细节请缩短对话或查阅活动日志。）"


def _context_tool_redact_enabled() -> bool:
    """上下文注入前是否压缩 tool 消息正文（``MINI_AGENT_CONTEXT_TOOL_REDACT``，默认开启）。"""
    raw = os.environ.get("MINI_AGENT_CONTEXT_TOOL_REDACT")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in ("true", "1", "yes")


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
        self._recalculate_tokens()

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
        """执行上下文压缩

        策略：
        - 保留：system prompt + 第 1 条用户消息
        - 保留：最近 2 轮对话（LLM 回复 + 工具结果）
        - 中间历史：替换为一行摘要
        """
        if len(self._messages) <= 4:
            return  # 消息太少，不需要压缩

        keep_start = 2  # system + first user
        keep_end = 4  # 最近 2 轮（每轮 = LLM 回复 + 工具结果）

        middle_start = keep_start
        middle_end = max(keep_start, len(self._messages) - keep_end)

        if middle_end <= middle_start:
            return  # 没有中间消息

        # 计算中间消息的统计
        middle_messages = self._messages[middle_start:middle_end]
        removed_tokens = sum(
            self._message_tokens(m) for m in middle_messages
        )
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
        self._messages[middle_start:middle_end] = [
            {"role": "system", "content": summary}
        ]

        self._compressed = True
        self._recalculate_tokens()

    def _compress_truncate(self) -> None:
        """从第三条消息起删除最旧条目，直至低于阈值或仅剩 system 与一条 user。"""
        guard = 0
        while self.needs_compression() and len(self._messages) > 2 and guard < 2000:
            guard += 1
            del self._messages[2]
        self._compressed = True
        self._recalculate_tokens()

    def inject_memory(self, memory: SessionMemory | None) -> None:
        """注入记忆摘要到 system prompt

        Args:
            memory: 会话记忆对象
        """
        memory_text = format_memory_for_prompt(memory)
        if not memory_text:
            return

        # 在 base system prompt 后面追加记忆
        self._system_prompt = f"{self._base_system_prompt}\n\n{memory_text}"

        # 更新 messages 中的 system prompt
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0]["content"] = self._system_prompt  # type: ignore

        self._recalculate_tokens()

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
        system_tokens = estimate_tokens(self._system_prompt)
        # 预留 10% 给输出
        output_reserve = int(self._context_window * 0.1)

        return max(0, self._context_window - tool_tokens - system_tokens - output_reserve)

    def _message_tokens(self, msg: ChatCompletionMessageParam) -> int:
        """估算单条消息的 token 数

        Args:
            msg: 消息对象

        Returns:
            估算的 token 数
        """
        tokens = estimate_tokens(msg.get("content"))  # type: ignore

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tokens += estimate_tokens(json.dumps(msg["tool_calls"]))

        # 角色标记额外开销
        tokens += 5

        return tokens

    def _recalculate_tokens(self) -> None:
        """重新计算总 token 估算"""
        self._total_tokens_estimate = sum(
            self._message_tokens(m) for m in self._messages
        )


__all__ = [
    "DefaultContextManager",
    "ContextBudgetExceeded",
    "estimate_tokens",
    "estimate_tool_tokens",
    "TokenEstimate",
]
