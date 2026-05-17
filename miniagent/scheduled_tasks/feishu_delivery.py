"""定时任务飞书投递：在 primary 绑定飞书时解析 receive_id 并发送最终回复。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from miniagent.scheduled_tasks.models import ScheduledTask
from miniagent.scheduled_tasks.resolve import should_run_feishu

if TYPE_CHECKING:
    from miniagent.engine.cli_state import CliLoopState


@dataclass(frozen=True)
class FeishuDeliveryTarget:
    """飞书 IM 投递目标（与 CLI 共享 session_key 记忆）。

    ``receive_chat_id``：IM API ``receive_id``（与入站 ``run_agent`` 一致，常为 ``oc_*`` 聊天室 ID）。
    ``mq_chat_id``：``MessageQueueManager`` 串行键（与 ``poll_server`` 的 ``dispatch(chat_id)`` 一致）。
    """

    receive_chat_id: str
    session_key: str
    mq_chat_id: str


def schedule_feishu_mirror_enabled() -> bool:
    """``MINIAGENT_SCHEDULE_FEISHU_MIRROR=0`` 时关闭 primary→绑定飞书镜像。"""
    raw = os.environ.get("MINIAGENT_SCHEDULE_FEISHU_MIRROR", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def schedule_feishu_last_chat_enabled() -> bool:
    """无通道绑定时，是否回退到 ``last_feishu_receive_chat_id``（默认关闭）。"""
    raw = os.environ.get("MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_valid_im_receive_id(chat_id: str) -> bool:
    from miniagent.feishu.poll_server import _is_valid_im_receive_id

    return _is_valid_im_receive_id((chat_id or "").strip())


def _channel_to_receive_id(channel_id: str) -> tuple[str | None, bool]:
    """返回 (receive_id, is_p2p_channel)。"""
    cid = (channel_id or "").strip()
    if cid.startswith("feishu_p2p:"):
        recv = cid.split(":", 1)[1].strip()
        return (recv if recv and _is_valid_im_receive_id(recv) else None), True
    if cid.startswith("feishu:"):
        recv = cid[len("feishu:") :].strip()
        return (recv if recv and _is_valid_im_receive_id(recv) else None), False
    return None, False


def _pick_bound_feishu_channel(bound: list[str]) -> str | None:
    p2p: list[str] = []
    groups: list[str] = []
    for ch in bound:
        if ch.startswith("feishu_p2p:"):
            p2p.append(ch)
        elif ch.startswith("feishu:"):
            groups.append(ch)
    if p2p:
        return p2p[0]
    if groups:
        return groups[0]
    return None


def _mq_chat_candidates(task: ScheduledTask, state: CliLoopState) -> list[str]:
    """与入站飞书一致的 ``message.chat_id`` 候选（优先最近活跃）。"""
    out: list[str] = []
    last = (state.get("last_feishu_receive_chat_id") or "").strip()
    if last and _is_valid_im_receive_id(last) and last not in out:
        out.append(last)
    explicit = (task.session.feishu_chat_id or "").strip()
    if explicit and _is_valid_im_receive_id(explicit) and explicit not in out:
        out.append(explicit)
    return out


def _resolve_mq_and_receive(
    task: ScheduledTask,
    state: CliLoopState,
    recv_im: str,
    *,
    is_p2p_bound: bool,
) -> tuple[str, str]:
    """解析 (mq_chat_id, receive_chat_id)。"""
    candidates = _mq_chat_candidates(task, state)
    if is_p2p_bound and candidates:
        mq = candidates[0]
        return mq, mq
    if not is_p2p_bound:
        return recv_im, recv_im
    return recv_im, recv_im


def resolve_feishu_delivery(
    task: ScheduledTask,
    *,
    session_key: str,
    feishu_recv: str | None,
    mq_chat: str,
    channel_router: Any,
    state: CliLoopState,
    feishu_runtime: Any,
) -> FeishuDeliveryTarget | None:
    """解析是否应向飞书投递；返回 None 表示仅 CLI。"""
    feishu_enabled = bool(state.get("feishu_enabled"))
    if not feishu_enabled:
        return None
    if feishu_runtime is None or not feishu_runtime.is_running():
        return None

    if should_run_feishu(session_key, feishu_recv, feishu_enabled=True):
        recv = (feishu_recv or "").strip()
        if not recv and session_key.startswith("feishu:"):
            recv = session_key[len("feishu:") :].strip()
        if recv:
            return FeishuDeliveryTarget(
                receive_chat_id=recv,
                session_key=session_key,
                mq_chat_id=mq_chat,
            )
        return None

    if not schedule_feishu_mirror_enabled():
        return None

    bound = channel_router.get_bound_channels(session_key)
    channel = _pick_bound_feishu_channel(bound)
    if not channel:
        if not schedule_feishu_last_chat_enabled():
            return None
        last = (state.get("last_feishu_receive_chat_id") or "").strip()
        if last and _is_valid_im_receive_id(last):
            return FeishuDeliveryTarget(
                receive_chat_id=last,
                session_key=session_key,
                mq_chat_id=last,
            )
        return None

    recv_im, is_p2p = _channel_to_receive_id(channel)
    if not recv_im:
        return None
    mq_id, receive_id = _resolve_mq_and_receive(
        task, state, recv_im, is_p2p_bound=is_p2p
    )
    return FeishuDeliveryTarget(
        receive_chat_id=receive_id,
        session_key=session_key,
        mq_chat_id=mq_id,
    )


async def send_scheduled_reply_to_feishu(
    feishu_config: Any,
    target: FeishuDeliveryTarget,
    task: ScheduledTask,
    reply: str,
) -> None:
    """将定时任务最终回复发往飞书（交互卡片）。"""
    from miniagent.feishu.poll_server import _send_reply

    body = f"[定时任务 {task.name}]\n{(reply or '').strip()}"
    if not body.strip():
        return
    await _send_reply(feishu_config, target.receive_chat_id, body)
