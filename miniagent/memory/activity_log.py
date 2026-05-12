"""Activity Log — 每日详细活动记录

写入 memory/YYYY-MM-DD.md，三层记忆架构的 Layer 2（流水账）。
记录每次 LLM 调用、工具调用详情、思考过程。

格式：
## 会话 <session_key>
### 用户输入
...
### LLM 调用 (第 N 轮)
- model: gpt-4o-mini
- tokens: 1234
- thinking: ...
### 工具调用
- tool: read_file
- intent: 读取配置文件
- args: {"path": "config.json"}
- result: {...} (前 500 字)
### 最终回复
...

详见 ``docs/MEMORY_SYSTEM.md``（Layer 2 流水账）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


class ActivityLogger:
    """每日活动日志，追加写入 Markdown 文件。

    三层记忆架构的 Layer 2（流水账），记录每次会话的完整活动：
    会话开始、LLM 调用、工具调用、最终回复、未完成状态。

    每个会话在同一天的日志中用 `## <session_key>` 分隔，
    同一会话的多次调用会追加在同一天文件中，不会覆盖。

    Example:
        logger = ActivityLogger()
        logger.log_session_start("cli-1", "帮我查天气")
        logger.log_llm_call("cli-1", 1, "gpt-4o-mini", 5, 3, "正在查询...")
        logger.log_tool_call("cli-1", "web_search", "搜索天气", {"query": "天气"}, "晴天", 150, True)
        logger.log_final_reply("cli-1", "今天晴天，温度 25°C")
    """

    def __init__(self, base_dir: str = "workspaces/memory") -> None:
        """创建活动日志实例。

        Args:
            base_dir: 日志文件存储目录
        """
        self._base_dir = base_dir

    def _get_today_path(self) -> str:
        """获取今日日志文件路径。

        文件以 YYYY-MM-DD.md 命名，自动创建目录。

        Returns:
            今日日志文件的完整路径
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(self._base_dir, exist_ok=True)
        return os.path.join(self._base_dir, f"{today}.md")

    def _read_today(self) -> str:
        """读取今日日志文件内容。

        Returns:
            日志内容，文件不存在时返回空字符串
        """
        path = self._get_today_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return ""

    def _append(self, content: str) -> None:
        """追加内容到今日日志文件。

        Args:
            content: 要追加的 Markdown 内容
        """
        path = self._get_today_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)

    def log_session_start(self, session_key: str, user_input: str, source: str = "cli") -> None:
        """记录会话开始。

        检查今日日志中是否已有该会话的 header，避免重复。
        然后追加用户输入内容。

        Args:
            session_key: 会话标识符
            user_input: 用户原始输入
            source: 来源标识（"cli" 或 "feishu"）
        """
        today = self._read_today()
        header = f"\n---\n## {session_key} ({source})\n\n"
        if f"## {session_key}" not in today:
            self._append(header)
        self._append(f"### 用户输入\n\n{user_input}\n\n")

    def log_llm_call(
        self,
        session_key: str,
        turn: int,
        model: str,
        message_count: int,
        tool_count: int,
        thinking: str | None,
        token_usage: dict | None = None,
    ) -> None:
        """记录 LLM 调用详情。

        记录模型名称、消息数、工具数、token 用量和思考内容（截断 500 字）。

        Args:
            session_key: 会话标识符
            turn: 当前轮次编号
            model: 使用的模型名称
            message_count: 上下文消息数
            tool_count: 可用工具数
            thinking: LLM 思考内容
            token_usage: token 使用量（含 prompt_tokens、completion_tokens）
        """
        lines = [f"### LLM 调用 (第 {turn} 轮)\n"]
        lines.append(f"- model: {model}")
        lines.append(f"- messages: {message_count}, tools: {tool_count}")
        if token_usage:
            lines.append(f"- tokens: prompt={token_usage.get('prompt_tokens', '?')}, completion={token_usage.get('completion_tokens', '?')}")
        if thinking:
            lines.append(f"- thinking: {thinking[:500]}")
        lines.append("")
        self._append("\n".join(lines))

    def log_tool_call(
        self,
        session_key: str,
        tool_name: str,
        intent: str,
        args: dict[str, Any],
        result: str,
        duration_ms: int,
        success: bool,
    ) -> None:
        """记录工具调用详情。

        记录工具名、意图、参数、结果（截断 500 字）、耗时和成功状态。

        Args:
            session_key: 会话标识符
            tool_name: 工具名称
            intent: 工具调用意图描述
            args: 工具调用参数
            result: 工具执行结果
            duration_ms: 执行耗时（毫秒）
            success: 是否成功
        """
        status = "ok" if success else "fail"
        lines = [f"### 工具调用: {tool_name} [{status}]\n"]
        lines.append(f"- intent: {intent}")
        lines.append(f"- args: {_short_json(args)}")
        # 结果截断到 500 字，避免日志过大
        preview = result[:500]
        if len(result) > 500:
            preview += f"\n... (共 {len(result)} 字)"
        lines.append(f"- result: {preview}")
        lines.append(f"- duration: {duration_ms}ms")
        lines.append("")
        self._append("\n".join(lines))

    def log_final_reply(self, session_key: str, reply: str) -> None:
        """记录最终回复（截断 1000 字）。

        Args:
            session_key: 会话标识符
            reply: LLM 最终回复内容
        """
        self._append(f"### 最终回复\n\n{reply[:1000]}\n\n")

    def log_incomplete(self, session_key: str, reason: str) -> None:
        """记录未完成状态（达到最大轮数等异常退出）。

        Args:
            session_key: 会话标识符
            reason: 未完成原因
        """
        self._append(f"### 未完成\n\n{reason}\n\n")


def _short_json(data: Any, max_len: int = 200) -> str:
    """将数据序列化为简短 JSON 字符串。

    超长时截断并追加 "..."，用于日志中参数预览。

    Args:
        data: 要序列化的数据
        max_len: 最大长度

    Returns:
        JSON 字符串（可能截断）
    """
    s = json.dumps(data, ensure_ascii=False)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
