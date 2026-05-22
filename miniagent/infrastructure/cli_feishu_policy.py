"""CLI 与飞书入站镜像策略（显示门控与绑定规范化）。

控制飞书消息是否写入 CLI transcript / 思考镜像，以及 ``.bind cli`` 群聊会话 ID 规范化。
Agent 路由与后台处理不受门控影响。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from miniagent.infrastructure.channel_router import ChannelRouter

CliFocusMode = Literal["general", "feishu_group"]


def is_feishu_group_session(session_key: str) -> bool:
    """会话键是否为飞书群聊独立会话（``feishu:oc_xxx``，非私聊）。"""
    sk = (session_key or "").strip()
    return sk.startswith("feishu:") and not sk.startswith("feishu_p2p:")


def cli_bound_session(router: ChannelRouter) -> str:
    """CLI 通道当前解析到的会话 ID。"""
    from miniagent.infrastructure.channel_router import ChannelRouter

    return router.resolve(ChannelRouter.CLI_CHANNEL)


def get_cli_focus_mode(router: ChannelRouter) -> CliFocusMode:
    """根据 CLI 绑定目标判断聚焦模式。"""
    if is_feishu_group_session(cli_bound_session(router)):
        return "feishu_group"
    return "general"


def normalize_bind_session_id(channel: str, raw_id: str) -> str:
    """规范化 ``.bind`` 目标会话 ID。

    - ``cli`` / ``feishu`` 绑群目标：裸 ``oc_*`` → ``feishu:oc_*``
    - ``ou_*`` 为用户 ID，不补 ``feishu:`` 前缀（私聊走 ``feishu_p2p:`` 通道）
    - 已是 ``feishu:`` / ``feishu_p2p:`` 则保持原样
    """
    sid = (raw_id or "").strip()
    if not sid:
        return sid
    ch = (channel or "").strip().lower()
    if ch not in ("cli", "feishu"):
        return sid
    if sid.startswith("feishu:") or sid.startswith("feishu_p2p:"):
        return sid
    if sid.startswith("oc_"):
        return f"feishu:{sid}"
    return sid


def should_allow_p2p_auto_bind(router: ChannelRouter) -> bool:
    """群聊聚焦模式下禁止私聊自动绑定到 active_session。"""
    return get_cli_focus_mode(router) != "feishu_group"


def should_sync_p2p_on_session_switch(router: ChannelRouter, target_session_id: str) -> bool:
    """切换到飞书群会话时不同步 feishu_p2p_synced_senders。

    在 ``sync_channel_router_to_session`` 中于 CLI 已绑定到 ``target`` 之后调用，
    故仅根据目标会话是否为群聊会话判断。
    """
    _ = router
    return not is_feishu_group_session((target_session_id or "").strip())


def p2p_bind_target_allowed(router: ChannelRouter, target_session_id: str) -> tuple[bool, str]:
    """群聊聚焦下拒绝将私聊绑定到任意飞书群会话。"""
    target = (target_session_id or "").strip()
    if not target:
        return False, "会话 ID 不能为空"
    if get_cli_focus_mode(router) != "feishu_group":
        return True, ""
    if is_feishu_group_session(target):
        return (
            False,
            "当前为飞书群聊聚焦模式：不允许将飞书私聊绑定到飞书群会话；请切换 CLI 到一般会话后再绑定私聊",
        )
    return True, ""


def should_mirror_feishu_to_cli(
    router: ChannelRouter,
    *,
    chat_type: str,
    chat_id: str,
    sender_id: str,
    session_key: str,
) -> bool:
    """是否将本条飞书入站/侧车信息写入 CLI transcript。"""
    from miniagent.infrastructure.channel_router import ChannelRouter

    mode = get_cli_focus_mode(router)
    cli_sk = cli_bound_session(router)
    sk = (session_key or "").strip()
    ct = (chat_type or "group").strip().lower()

    if mode == "feishu_group":
        if ct == "group":
            return sk == cli_sk
        return False

    # general：群聊不镜像；私聊仅当与 CLI 同会话
    if ct == "group":
        return False
    if ct == "p2p":
        p2p_ch = f"{ChannelRouter.FEISHU_P2P_PREFIX}{(sender_id or '').strip()}"
        return router.resolve(p2p_ch) == cli_sk
    return False


def focus_mode_status_line(router: ChannelRouter) -> str:
    """供 ``.bind status`` 附加的聚焦模式说明。"""
    mode = get_cli_focus_mode(router)
    if mode == "feishu_group":
        sk = cli_bound_session(router)
        short = sk[7:19] if sk.startswith("feishu:") else sk[:12]
        return f"  CLI 聚焦: 飞书群聊 ({short}…)，私聊不入 CLI"
    return "  CLI 聚焦: 一般模式（私聊同 CLI 会话可见；群聊仅后台处理）"
