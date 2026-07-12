"""将单条 :class:`~miniagent.scheduled_tasks.models.ScheduledTask` 编译为可 ``await`` 的协程及队列 chat_id。

不负责持久化更新；由 :mod:`miniagent.scheduled_tasks.ticker` 在任务结束后写回 ``last_run_at`` / ``next_run_at``。

执行路径最终调用 ``UnifiedEngine``，与会话人工消息共用队列模型（见 ``docs/ARCHITECTURE.md``）。"""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.contracts.messages import InboundMessage
from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.logger import get_logger
from miniagent.scheduled_tasks.feishu_delivery import (
    resolve_feishu_delivery,
    send_scheduled_reply_to_feishu,
)
from miniagent.scheduled_tasks.models import ScheduledTask
from miniagent.scheduled_tasks.resolve import resolve_execution_target, should_run_feishu
from miniagent.types.error_prefix import ERROR_PREFIX

_logger = get_logger(__name__)

# 错误文本截断最大长度（飞书消息限制）
MAX_ERROR_TEXT_LENGTH = 4000
SCHEDULER_CHANNEL = "scheduler"
SCHEDULER_SENDER_ID = "scheduler"


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """Normalized scheduled turn plus its behavior-preserving Agent handler."""

    message: InboundMessage
    queue_key: str
    run: Callable[[InboundMessage], Awaitable[str | None]]


def _emit_cli(ctx: ApplicationContainer, line: str) -> None:
    """定时任务日志：优先写入全屏 CLI transcript，否则 ``print``。"""
    fn = ctx.cli_transcript_append
    if fn is not None:
        try:
            fn("class:cli-muted", line if line.endswith("\n") else line + "\n")
        except Exception:
            print(line, flush=True)
    else:
        print(line, flush=True)


def build_scheduled_job(
    ctx: ApplicationContainer,
    state: CliLoopState,
    task: ScheduledTask,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
) -> ScheduledJob:
    """Compile one task into a normalized inbound turn and Agent handler.

    构建执行协程，最终调用 UnifiedEngine.run_agent_with_thinking，
    并在完成后可选向飞书镜像推送执行结果。

    Args:
        ctx: 运行时上下文（含 engine、channel_router、message_queue）
        state: CLI 循环状态（含会话管理器）
        task: 要执行的任务定义
        skill_toolboxes: 技能工具箱列表（用于 Agent 执行）
        skill_prompts: 技能提示列表（用于 Agent 执行）

    Returns:
        标准入站消息、队列键与执行 handler。

    Note:
        - 执行路径与会话人工消息共用队列模型
        - 不负责持久化更新（由 ticker 写回 last_run_at/next_run_at）
        - 错误文本会被截断到 MAX_ERROR_TEXT_LENGTH
    """
    channel_router = ctx.channel_router
    session_key, feishu_recv, mq_chat = resolve_execution_target(
        task, channel_router=channel_router, state=state
    )
    delivery = resolve_feishu_delivery(
        task,
        session_key=session_key,
        feishu_recv=feishu_recv,
        mq_chat=mq_chat,
        channel_router=channel_router,
        state=state,
        feishu_runtime=ctx.feishu,
    )
    outbound_channels = ctx.outbound_channels
    if delivery is not None:
        mq_chat = delivery.mq_chat_id
        feishu_recv = delivery.receive_chat_id
        session_key = delivery.session_key

    idempotency_key = None
    if task.next_run_at is not None:
        idempotency_key = f"{task.id}:{float(task.next_run_at):.6f}"
    message = InboundMessage.create(
        channel=SCHEDULER_CHANNEL,
        conversation_id=mq_chat,
        sender_id=SCHEDULER_SENDER_ID,
        content=task.prompt or f"[scheduled task {task.name}]",
        session_key=session_key,
        idempotency_key=idempotency_key,
        metadata={
            "task_id": task.id,
            "task_name": task.name,
            "schedule_kind": task.schedule.kind,
            "schedule_timezone": task.schedule.timezone,
            "session_mode": task.session.mode,
            "queue_key": mq_chat,
            "feishu_receive_chat_id": feishu_recv,
        },
    )

    async def _run(inbound: InboundMessage) -> str | None:
        """执行单条定时任务：构造带前缀 prompt 并走 ``run_agent_with_thinking``。"""
        engine = ctx.engine
        registry = ctx.registry
        monitor = ctx.monitor
        is_fs = delivery is not None or should_run_feishu(
            session_key,
            feishu_recv,
            feishu_enabled=bool(state.get("feishu_enabled")),
        )
        feishu_cfg = ctx.feishu.get_config() if is_fs and ctx.feishu else None
        from miniagent.infrastructure.timezone_config import now_in_process_tz
        from miniagent.scheduled_tasks.store import effective_task_timezone

        sched_tz = effective_task_timezone(task)
        now = now_in_process_tz()
        prompt = (
            f"[定时任务 {task.name} | 调度时区 {sched_tz} | "
            f"当前本地 {now.strftime('%Y-%m-%d %H:%M:%S')}]\n"
            f"{inbound.content}"
        )
        routed = replace(inbound, content=prompt)
        routed_session_key = routed.session_key or session_key
        _emit_cli(ctx, f"⏰ 定时任务开始: {task.id} → {routed_session_key}")

        try:
            reply = await engine.run_agent_with_thinking(
                prompt,
                routed_session_key,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                is_feishu=is_fs,
                registry=registry,
                monitor=monitor,
                session_manager=state.get("session_manager"),
                feishu_config=feishu_cfg,
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory=ctx.memory,
                knowledge_registry=ctx.knowledge_registry,
                client=ctx.openai_client,
                feishu_receive_chat_id=feishu_recv,
                cli_loop_state=state,
            )
            preview = (reply or "").strip().replace("\n", " ")[:200]
            _emit_cli(ctx, f"⏰ 定时任务完成: {task.id} — {preview}")
            if delivery is not None and feishu_cfg and (reply or "").strip():
                try:
                    await send_scheduled_reply_to_feishu(
                        delivery,
                        task,
                        reply or "",
                        outbound_channels=outbound_channels,
                    )
                except Exception:
                    _logger.exception("定时任务飞书投递失败: %s", task.id)
            return None
        except Exception as e:
            _logger.exception("定时任务执行失败: %s", task.id)
            _emit_cli(ctx, f"{ERROR_PREFIX} 定时任务失败 {task.id}: {e}")
            err_text = f"{e!s}\n{traceback.format_exc()}"[:MAX_ERROR_TEXT_LENGTH]
            if delivery is not None and feishu_cfg:
                try:
                    await send_scheduled_reply_to_feishu(
                        delivery,
                        task,
                        f"定时任务执行失败:\n{err_text[:3500]}",
                        outbound_channels=outbound_channels,
                    )
                except Exception:
                    _logger.exception("定时任务飞书失败通知发送失败: %s", task.id)
            return err_text

    return ScheduledJob(message=message, queue_key=mq_chat, run=_run)


__all__ = [
    "MAX_ERROR_TEXT_LENGTH",
    "SCHEDULER_CHANNEL",
    "SCHEDULER_SENDER_ID",
    "ScheduledJob",
    "build_scheduled_job",
]
