"""Mini Agent Python — 飞书消息处理器 (Phase 8)

将飞书消息路由到 Agent 引擎并返回回复。

工作流程：
1. 接收飞书消息事件（文本）
2. 提取消息内容和会话信息
3. 调用 Agent 引擎处理
4. 返回回复文本

支持的功能：
- 消息去重（复用 poll_server 的去重机制）
- 会话隔离（每个飞书聊天 ID 独立会话）
- 工具监控和统计
- 技能系统提示注入
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.core.agent import run_agent
from src.core.registry import DefaultToolRegistry
from src.core.monitor import DefaultToolMonitor
from src.core.logger import get_logger
from src.types.tool import Toolbox
from src.types.skill import Skill

_logger = get_logger(__name__)


def create_feishu_handler(
    registry: DefaultToolRegistry,
    monitor: DefaultToolMonitor,
    toolboxes: list[Toolbox],
    skills: list[Skill],
    skill_prompts: list[str] | None = None,
    send_thinking: Callable[[str, str], Awaitable[None]] | None = None,
) -> Callable[[str, str, str], Awaitable[str]]:
    """创建飞书消息处理器。

    Args:
        registry: 工具注册表
        monitor: 工具监控器
        toolboxes: 工具箱列表
        skills: 已加载技能列表
        skill_prompts: 技能系统提示
        send_thinking: 发送思考过程 (chat_id, thinking_text)

    Returns:
        消息处理函数 (content, chatId, senderId) => reply
    """

    # 会话历史缓存
    _conversation_histories: dict[str, list[dict[str, str]]] = {}

    async def handler(content: str, chat_id: str, sender_id: str) -> str:
        """处理飞书消息。

        Args:
            content: 消息内容
            chat_id: 聊天 ID
            sender_id: 发送者 ID

        Returns:
            Agent 回复文本
        """
        # 获取或创建会话历史
        if chat_id not in _conversation_histories:
            _conversation_histories[chat_id] = []

        history = _conversation_histories[chat_id]

        try:
            # 创建 on_thinking 回调（捕获 chat_id）
            on_thinking = None
            if send_thinking:
                async def _think(text: str) -> None:
                    await send_thinking(chat_id, text)
                on_thinking = _think

            # 调用 Agent
            reply = await run_agent(
                content,
                registry=registry,
                monitor=monitor,
                toolboxes=toolboxes,
                skip_planning=False,
                agent_config={
                    "session_key": chat_id,
                    "conversation_history": history,
                    "debug": False,
                },
                system_prompt="\n\n".join(skill_prompts) if skill_prompts else None,
                on_thinking=on_thinking,
            )

            # 更新对话历史
            history.append({"role": "user", "content": content})
            history.append({"role": "assistant", "content": reply})

            # 限制历史长度
            if len(history) > 40:
                _conversation_histories[chat_id] = history[-40:]

            return reply

        except Exception as e:
            _logger.error("处理失败: %s", e)
            return f"⚠️ 处理失败: {e}"

    return handler


__all__ = ["create_feishu_handler"]
