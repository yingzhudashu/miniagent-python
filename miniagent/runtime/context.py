"""RuntimeContext — 单进程内 Agent 运行所需的组合依赖。

由入口（如 ``compat.unified_entry``）在启动时 **一次性构造**，再经 ``unified_main``、
CLI 主循环与飞书消息 handler 的闭包向下传递。设计目标：

- **可测试**：单元测试可注入伪造的 registry / engine / memory，无需改动全局单例。
- **边界清晰**：引擎、队列、路由器、记忆、LLM 客户端的生命周期与当前 OS 进程对齐。

历史上曾把这些依赖挂在已移除的 ``unified`` 模块全局上。新代码应 **优先** 通过显式
``ctx`` 参数或闭包传递依赖；``get_runtime_context()`` 仅作嵌入/诊断时的只读回退，
勿在业务模块中缓存第二个隐式上下文。

构造顺序上，入口通常在创建本对象**之前**调用 ``load_secrets_from_project_root()``，以便
敏感凭据加载完成，配置通过 JSON 格式传递。``unified_entry`` 构造后会调用
``set_runtime_context(ctx)``；``shutdown_runtime`` 结束时清空该登记。

整体关系图见 ``docs/ARCHITECTURE.md``。

字段提示：

- ``clawhub``：入口注入的 ClawHub 客户端；部分工具仍可能直接调用工厂函数，与注入并存。
- ``openai_client``：AsyncOpenAI 兼容客户端（通常 ``get_shared_async_openai()``）；为 ``None`` 时执行链回落进程内共享工厂。
  ``reload_runtime_config()`` 会同步刷新本字段。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.types.agent import ToolMonitorProtocol
    from miniagent.types.memory_context import MemoryContextProtocol
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
        memory_context: 记忆上下文服务（``DefaultMemoryContext``）
        openai_client: LLM 客户端（``AsyncOpenAI`` 或兼容实现）；``None`` 表示使用默认工厂；
            ``reload_runtime_config()`` 会按最新配置重建并更新本字段
        create_feishu_handler_factory: ``(state: CliLoopState) -> handler``；
            在 ``unified_main`` 内赋值为闭包，内部调用 ``create_feishu_handler(state, ctx, ...)``
        cli_transcript_append_ansi: 全屏 CLI 时注册 ``(ansi_obj) -> None``，
            将 ANSI 渲染内容写入 transcript 并管理滚动（与 ``cli_transcript_append`` 并列）。
        cli_transcript_append: 全屏 CLI 时注册 ``(style_cls, text) -> None``，
            将飞书/侧路输出写入 transcript；未注册时相关代码回退到 ``print``。
        cli_transcript_coordinator: ``CliTranscriptCoordinator`` 实例；
            并行会话模式下协调多路 transcript 写入（全屏 CLI 启动后赋值）。
        scheduled_tasks_ticker: 定时任务后台 ``asyncio.Task``（``scheduled_tasks_loop``）。
        scheduled_tasks_stop_event: 与 ticker 协作退出的 ``asyncio.Event``。
        skills_watch_task: ``MINIAGENT_SKILLS_WATCH`` 目录监视 ``asyncio.Task``。
        skills_watch_stop_event: 技能目录监视协作退出的 ``asyncio.Event``。
        shutdown_tracked_tasks: ``tick_once`` 等 fire-and-forget 任务集合；
            由 :meth:`register_shutdown_tracked_task` 登记，
            :func:`miniagent.engine.shutdown.shutdown_runtime` 在退出时 cancel 并 await。

    Methods:
        register_shutdown_tracked_task: 登记需在进程退出时取消的后台任务（完成时自动移除）。
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
    memory_context: MemoryContextProtocol
    openai_client: Any | None = None
    create_feishu_handler_factory: Callable[..., Any] | None = field(default=None, repr=False)
    cli_transcript_append_ansi: Callable[[Any], None] | None = field(default=None, repr=False)
    cli_transcript_append: Callable[[str, str], None] | None = field(default=None, repr=False)
    cli_transcript_coordinator: Any | None = field(default=None, repr=False)
    scheduled_tasks_ticker: asyncio.Task[Any] | None = field(default=None, repr=False)
    scheduled_tasks_stop_event: asyncio.Event | None = field(default=None, repr=False)
    skills_watch_task: asyncio.Task[Any] | None = field(default=None, repr=False)
    skills_watch_stop_event: asyncio.Event | None = field(default=None, repr=False)
    shutdown_tracked_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)

    def register_shutdown_tracked_task(self, task: asyncio.Task[Any]) -> None:
        """登记需在进程退出时取消并等待的任务（完成时自动移除）。

        若 ``task`` 已完成，或已在 ``shutdown_tracked_tasks`` 中，则为 no-op
        （避免重复 ``done`` 回调）。进程关停期间新登记的任务可能不被当次
        ``shutdown_runtime`` 快照捕获，属可接受的极端竞态。
        """
        if task.done() or task in self.shutdown_tracked_tasks:
            return
        self.shutdown_tracked_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self.shutdown_tracked_tasks.discard(t)

        task.add_done_callback(_done)


# ============================================================================
# 进程级登记（显式传参为主，以下为只读回退与测试清理）
# ============================================================================

_default_context: RuntimeContext | None = None


def get_runtime_context() -> RuntimeContext | None:
    """返回 ``unified_entry`` 登记的进程级上下文；未启动时为 ``None``。

    新代码应优先使用显式 ``ctx`` 参数。本函数适用于嵌入场景、诊断脚本等
    无法改签名传递 ``ctx`` 的只读回退，勿在业务模块中替代依赖注入。
    """
    return _default_context


def set_runtime_context(ctx: RuntimeContext) -> None:
    """登记当前进程的 ``RuntimeContext``（``compat.unified_entry`` 在构造后调用）。

    同一进程内重复调用会覆盖先前登记；嵌入 ``unified_main(ctx)`` 时调用方
    若不经 ``unified_entry``，应自行调用本函数以便 ``get_runtime_context()`` 可用。
    """
    global _default_context
    _default_context = ctx


def reset_runtime_context_for_tests() -> None:
    """清空进程级 ``RuntimeContext`` 登记。

    供 pytest teardown 与 ``shutdown_runtime`` 收尾使用；正常运行时进程即将退出，
    清空仅为同进程内重复启动或测试隔离。
    """
    global _default_context
    _default_context = None


__all__ = [
    "RuntimeContext",
    "get_runtime_context",
    "set_runtime_context",
    "reset_runtime_context_for_tests",
]
