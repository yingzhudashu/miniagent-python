"""RuntimeContext — 单进程内 Agent 运行所需的组合依赖。

由入口（如 ``engine.main.unified_main``）在启动时 **一次性构造**，再经 ``unified_main``、
CLI 主循环与飞书消息 handler 的闭包向下传递。设计目标：

- **可测试**：单元测试可注入伪造的 registry / engine / memory，无需改动全局单例。
- **边界清晰**：引擎、队列、路由器、记忆、LLM 客户端的生命周期与当前 OS 进程对齐。

历史上曾把这些依赖挂在已移除的 ``unified`` 模块全局上；新代码请只通过本上下文或显式参数传递依赖。

构造顺序上，入口通常在创建本对象**之前**调用 ``load_secrets_from_project_root()``，以便
敏感凭据加载完成，配置通过JSON格式传递。

整体关系图见 ``docs/ARCHITECTURE.md``。

字段提示：

- ``clawhub``：入口注入的 ClawHub 客户端；部分工具仍可能直接调用工厂函数，与注入并存。
- ``openai_client``：AsyncOpenAI 兼容客户端（通常 ``get_shared_async_openai()``）；为 ``None`` 时执行链回落进程内共享工厂。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.types.agent import ToolMonitorProtocol
    from miniagent.types.protocols import (
        ActivityLogProtocol,
        ChannelRouterProtocol,
        FeishuRuntimeProtocol,
        KeywordIndexProtocol,
        MemoryStoreProtocol,
        MessageQueueProtocol,
        UnifiedEngineProtocol,
    )
    from miniagent.types.skill import ClawHubClientProtocol, SkillRegistryProtocol
    from miniagent.types.tool import ToolRegistryProtocol


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
        create_feishu_handler_factory: ``(toolboxes, prompts, state) -> handler`` 或
            ``(text_handler, media_handler)`` 元组；在 ``unified_main`` 内赋值，闭包捕获本上下文
        cli_transcript_append_ansi: 全屏 CLI 时注册 ``(ansi_obj) -> None``，
            将 ANSI 渲染内容写入 transcript 并管理滚动（与 ``cli_transcript_append`` 并列）。
        cli_transcript_append: 全屏 CLI 时注册 ``(style_cls, text) -> None``，
            将飞书/侧路输出写入 transcript；未注册时相关代码回退到 ``print``。
        skills_watch_task / skills_watch_stop_event: ``MINIAGENT_SKILLS_WATCH`` 目录监视任务。
    """

    registry: ToolRegistryProtocol
    monitor: ToolMonitorProtocol
    skill_registry: SkillRegistryProtocol
    clawhub: ClawHubClientProtocol | None
    engine: UnifiedEngineProtocol
    channel_router: ChannelRouterProtocol
    message_queue: MessageQueueProtocol
    feishu: FeishuRuntimeProtocol | None
    memory_store: MemoryStoreProtocol
    activity_log: ActivityLogProtocol
    keyword_index: KeywordIndexProtocol
    openai_client: Any | None = None
    create_feishu_handler_factory: Callable[..., Any] | None = field(default=None, repr=False)
    cli_transcript_append_ansi: Callable[[Any], None] | None = field(default=None, repr=False)
    cli_transcript_append: Callable[[str, str], None] | None = field(default=None, repr=False)
    #: 定时任务后台循环（``miniagent.scheduled_tasks``）；可选便于退出时 cancel
    scheduled_tasks_ticker: asyncio.Task[Any] | None = field(default=None, repr=False)
    scheduled_tasks_stop_event: asyncio.Event | None = field(default=None, repr=False)
    #: 技能目录监视（``MINIAGENT_SKILLS_WATCH``）；退出时由 ``shutdown_runtime`` 停止
    skills_watch_task: asyncio.Task[Any] | None = field(default=None, repr=False)
    skills_watch_stop_event: asyncio.Event | None = field(default=None, repr=False)
    #: ``tick_once`` 等 fire-and-forget 任务登记，供 :func:`miniagent.engine.shutdown.shutdown_runtime` 取消
    shutdown_tracked_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)

    def register_shutdown_tracked_task(self, task: asyncio.Task[Any]) -> None:
        """登记需在进程退出时取消并等待的任务（完成时自动移除）。"""
        self.shutdown_tracked_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            """任务结束后从关停登记集合移除，避免泄漏。"""
            self.shutdown_tracked_tasks.discard(t)

        task.add_done_callback(_done)


# ============================================================================
# 测试辅助函数
# ============================================================================

_default_context: RuntimeContext | None = None


def get_runtime_context() -> RuntimeContext | None:
    """获取进程级 RuntimeContext 单例（如果已初始化）。"""
    return _default_context


def set_runtime_context(ctx: RuntimeContext) -> None:
    """设置进程级 RuntimeContext 单例。"""
    global _default_context
    _default_context = ctx


def reset_runtime_context_for_tests() -> None:
    """清空 RuntimeContext 缓存，仅供测试使用。"""
    global _default_context
    _default_context = None


__all__ = [
    "RuntimeContext",
    "get_runtime_context",
    "set_runtime_context",
    "reset_runtime_context_for_tests",
]
