"""RuntimeContext — 单进程内 Agent 运行所需的组合依赖。

由入口（如 ``compat.unified_entry``）在启动时 **一次性构造**，再经 ``unified_main``、
CLI 主循环与飞书消息 handler 的闭包向下传递。设计目标：

- **可测试**：单元测试可注入伪造的 registry / engine / memory，无需改动全局单例。
- **边界清晰**：引擎、队列、路由器、记忆、LLM 客户端的生命周期与当前 OS 进程对齐。

历史上曾把这些依赖挂在已移除的 ``unified`` 模块全局上；新代码请只通过本上下文或显式参数传递依赖。

字段提示：

- ``clawhub``：入口注入的 ClawHub 客户端；部分工具仍可能直接调用工厂函数，与注入并存。
- ``openai_client``：AsyncOpenAI 兼容客户端（通常 ``get_shared_async_openai()``）；为 ``None`` 时执行链回落进程内共享工厂。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RuntimeContext:
    """进程级运行时上下文（组合根）。

    Attributes:
        registry: 工具注册表
        monitor: 工具调用监控
        skill_registry: 技能注册表
        clawhub: ClawHub 客户端（技能市场等；与 ``registry`` 并列；工具层可逐步改为仅用此实例）
        engine: 统一编排引擎（``UnifiedEngine``）
        channel_router: 通道与会话绑定路由器
        message_queue: 多聊天室消息队列（CLI 与飞书共用）
        feishu: 飞书 WebSocket 任务生命周期
        memory_store: 跨会话记忆持久化（``DefaultMemoryStore``）
        activity_log: 活动日志写入器（``ActivityLogger``）
        keyword_index: 关键词检索索引（``KeywordIndex``）
        openai_client: LLM 客户端（``AsyncOpenAI`` 或兼容实现）；``None`` 表示使用默认工厂
        create_feishu_handler_factory: ``(toolboxes, prompts, state) -> handler``，
            在 ``unified_main`` 内赋值，闭包捕获本上下文
        cli_transcript_append: 全屏 CLI 时注册 ``(style_cls, text) -> None``，
            将飞书/侧路输出写入 transcript；未注册时相关代码回退到 ``print``。
    """

    registry: Any
    monitor: Any
    skill_registry: Any
    clawhub: Any
    engine: Any
    channel_router: Any
    message_queue: Any
    feishu: Any
    memory_store: Any
    activity_log: Any
    keyword_index: Any
    openai_client: Any | None = None
    create_feishu_handler_factory: Callable[..., Any] | None = field(default=None, repr=False)
    cli_transcript_append: Callable[[str, str], None] | None = field(default=None, repr=False)
