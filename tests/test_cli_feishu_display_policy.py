"""CLI 与飞书多渠道显示隔离策略测试。"""

from __future__ import annotations

import pytest

from miniagent.engine.cli_commands import cmd_bind, sync_channel_router_to_session
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.cli_feishu_policy import (
    get_cli_focus_mode,
    normalize_bind_session_id,
    should_allow_p2p_auto_bind,
    should_mirror_feishu_to_cli,
    should_sync_p2p_on_session_switch,
)


@pytest.fixture
def router() -> ChannelRouter:
    r = ChannelRouter()
    r.bind(ChannelRouter.CLI_CHANNEL, "default")
    r.set_primary("default")
    return r


def test_normalize_bind_session_id_oc_prefix() -> None:
    assert normalize_bind_session_id("cli", "oc_abc123") == "feishu:oc_abc123"
    assert normalize_bind_session_id("cli", "feishu:oc_abc123") == "feishu:oc_abc123"
    assert normalize_bind_session_id("cli", "default") == "default"
    assert normalize_bind_session_id("cli", "ou_user123") == "ou_user123"
    assert normalize_bind_session_id("feishu", "oc_grp") == "feishu:oc_grp"


def test_general_mode_group_not_mirrored_p2p_mirrored_when_bound(router: ChannelRouter) -> None:
    router.bind("feishu_p2p:ou_a", "default")
    assert get_cli_focus_mode(router) == "general"
    assert not should_mirror_feishu_to_cli(
        router,
        chat_type="group",
        chat_id="oc_g1",
        sender_id="ou_x",
        session_key="feishu:oc_g1",
    )
    assert should_mirror_feishu_to_cli(
        router,
        chat_type="p2p",
        chat_id="ou_a",
        sender_id="ou_a",
        session_key="default",
    )


def test_feishu_group_focus_only_bound_group_mirrored(router: ChannelRouter) -> None:
    router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_A")
    router.set_primary("feishu:oc_A")
    assert get_cli_focus_mode(router) == "feishu_group"
    assert should_mirror_feishu_to_cli(
        router,
        chat_type="group",
        chat_id="oc_A",
        sender_id="ou_x",
        session_key="feishu:oc_A",
    )
    assert not should_mirror_feishu_to_cli(
        router,
        chat_type="group",
        chat_id="oc_B",
        sender_id="ou_x",
        session_key="feishu:oc_B",
    )
    assert not should_mirror_feishu_to_cli(
        router,
        chat_type="p2p",
        chat_id="ou_p",
        sender_id="ou_p",
        session_key="feishu_p2p:ou_p",
    )


def test_group_focus_blocks_p2p_auto_bind_and_sync(router: ChannelRouter) -> None:
    router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_focus")
    assert not should_allow_p2p_auto_bind(router)
    assert not should_sync_p2p_on_session_switch(router, "feishu:oc_focus")
    assert should_sync_p2p_on_session_switch(router, "default")


def test_cmd_bind_cli_normalizes_oc_id(router: ChannelRouter) -> None:
    out = cmd_bind(router, ["cli", "oc_mygroup"])
    assert "feishu:oc_mygroup" in out
    assert router.resolve(ChannelRouter.CLI_CHANNEL) == "feishu:oc_mygroup"
    assert router.primary == "feishu:oc_mygroup"
    assert (
        router.resolve_feishu_message("oc_mygroup", "ou_x", "group")
        == "feishu:oc_mygroup"
    )


def test_p2p_mirror_after_auto_bind_resolve(router: ChannelRouter) -> None:
    """首条私聊在 auto_bind 后应与 CLI 同会话，应镜像到 CLI。"""
    router.bind(ChannelRouter.CLI_CHANNEL, "default")
    router.set_primary("default")
    channel_id = "feishu_p2p:ou_new"
    assert router.resolve(channel_id) == channel_id
    router.bind(channel_id, "default")
    assert should_mirror_feishu_to_cli(
        router,
        chat_type="p2p",
        chat_id="ou_new",
        sender_id="ou_new",
        session_key="default",
    )


def test_cmd_bind_feishu_rejected_in_group_focus(router: ChannelRouter) -> None:
    router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_g")
    out = cmd_bind(router, ["feishu", "ou_user", "feishu:oc_g"])
    assert "❌" in out
    assert "群聊聚焦" in out


def test_sync_channel_router_skips_p2p_on_group_switch(router: ChannelRouter) -> None:
    synced = {"ou_auto"}
    router.bind("feishu_p2p:ou_auto", "default")
    sync_channel_router_to_session(router, "feishu:oc_new", synced)
    assert router.resolve(ChannelRouter.CLI_CHANNEL) == "feishu:oc_new"
    assert router.resolve("feishu_p2p:ou_auto") == "default"
