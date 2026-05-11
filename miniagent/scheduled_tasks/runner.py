"""将单条 :class:`~miniagent.scheduled_tasks.models.ScheduledTask` 编译为可 ``await`` 的协程及队列 chat_id。

不负责持久化更新；由 :mod:`miniagent.scheduled_tasks.ticker` 在任务结束后写回 ``last_run_at`` / ``next_run_at``。

执行路径最终调用 ``UnifiedEngine``，与会话人工消息共用队列模型（见 ``docs/ARCHITECTURE.md``）。"""
from __future__ import annotations

import traceback
from typing import Any

from miniagent.engine.cli_state import CliLoopState
from miniagent.infrastructure.logger import get_logger
from miniagent.runtime.context import RuntimeContext
from miniagent.scheduled_tasks.models import ScheduledTask
from miniagent.scheduled_tasks.resolve import resolve_execution_target, should_run_feishu

_logger = get_logger(__name__)


def _emit_cli(ctx: RuntimeContext, line: str) -> None:
    """定时任务日志：优先写入全屏 CLI transcript，否则 ``print``。"""
    fn = ctx.cli_transcript_append
    if fn is not None:
        try:
            fn("class:cli-muted", line if line.endswith("\n") else line + "\n")
        except Exception:
            print(line, flush=True)
    else:
        print(line, flush=True)


def build_run_scheduled_job_coro(
    ctx: RuntimeContext,
    state: CliLoopState,
    task: ScheduledTask,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
) -> tuple[Any, str]:
    """返回 (协程, message_queue 用的 chat_id)。协程执行完毕返回 ``None`` 或错误摘要字符串。"""
    channel_router = ctx.channel_router
    session_key, feishu_recv, mq_chat = resolve_execution_target(
        task, channel_router=channel_router, state=state
    )

    async def _run() -> str | None:
        engine = ctx.engine
        registry = ctx.registry
        monitor = ctx.monitor
        is_fs = should_run_feishu(
            session_key,
            feishu_recv,
            feishu_enabled=bool(state.get("feishu_enabled")),
        )
        feishu_cfg = ctx.feishu.get_config() if is_fs else None
        prompt = f"[定时任务 {task.name}]\n{task.prompt}"
        _emit_cli(ctx, f"⏰ 定时任务开始: {task.id} → {session_key}")

        try:
            reply = await engine.run_agent_with_thinking(
                prompt,
                session_key,
                skill_toolboxes,
                "\n\n".join(skill_prompts) if skill_prompts else None,
                is_feishu=is_fs,
                registry=registry,
                monitor=monitor,
                session_manager=state.get("session_manager"),
                feishu_config=feishu_cfg,
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                feishu_receive_chat_id=feishu_recv,
                cli_loop_state=state,
            )
            preview = (reply or "").strip().replace("\n", " ")[:200]
            _emit_cli(ctx, f"⏰ 定时任务完成: {task.id} — {preview}")
            return None
        except Exception as e:
            _logger.exception("定时任务执行失败: %s", task.id)
            _emit_cli(ctx, f"❌ 定时任务失败 {task.id}: {e}")
            return f"{e!s}\n{traceback.format_exc()}"[:4000]

    return _run(), mq_chat
