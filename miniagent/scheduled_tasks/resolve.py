"""解析定时任务应在哪个 ``session_key`` 上执行，以及如何映射到消息队列 ``chat_id`` 与飞书收信方。

``primary`` / ``fixed`` / ``ephemeral`` 三种模式对用户行为的含义见 ``docs/USER_GUIDE.md``。"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from miniagent.scheduled_tasks.models import ScheduledTask

if TYPE_CHECKING:
    from miniagent.engine.cli_state import CliLoopState


def resolve_execution_target(
    task: ScheduledTask,
    *,
    channel_router: Any,
    state: CliLoopState,
) -> tuple[str, str | None, str]:
    """返回 (session_key, feishu_receive_chat_id_or_None, message_queue_chat_id)。

    message_queue_chat_id：CLI 用 ``__cli__``；飞书群/会话用 API 的 chat_id。
    """
    sess = task.session
    mode = sess.mode

    if mode == "ephemeral":
        sid = f"sched_{task.id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        return sid, None, "__cli__"

    if mode == "fixed":
        session_key = (sess.session_id or "").strip() or "default"
    else:
        # primary
        primary = getattr(channel_router, "primary", None)
        if primary:
            session_key = str(primary)
        else:
            session_key = (state.get("active_session_id") or "").strip() or "default"

    feishu_recv: str | None = None
    mq_chat = "__cli__"

    explicit = (sess.feishu_chat_id or "").strip()
    if session_key.startswith("feishu:"):
        feishu_recv = explicit or session_key[len("feishu:") :]
        mq_chat = feishu_recv
    elif session_key.startswith("feishu_p2p:"):
        feishu_recv = explicit
        if feishu_recv:
            mq_chat = feishu_recv
        else:
            # 无私聊 chat_id 时仍走 CLI 队列，但飞书思考无法推送
            mq_chat = "__cli__"

    return session_key, feishu_recv, mq_chat


def should_run_feishu(
    session_key: str,
    feishu_receive_chat_id: str | None,
    *,
    feishu_enabled: bool,
) -> bool:
    """是否按「飞书通道」跑 ``run_agent_with_thinking``（影响回复推送与 is_feishu 标志）。"""
    if not feishu_enabled:
        return False
    if session_key.startswith("feishu:"):
        return bool((feishu_receive_chat_id or "").strip() or session_key[len("feishu:") :].strip())
    if session_key.startswith("feishu_p2p:"):
        return bool((feishu_receive_chat_id or "").strip())
    return False
