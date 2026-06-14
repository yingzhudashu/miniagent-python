"""飞书 handler 命令、绑定与媒体路径测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.feishu_handler import create_feishu_handler
from miniagent.feishu.types import FeishuInboundText
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.types.error_prefix import SUCCESS_PREFIX
from tests.test_feishu_handler_cli_mirror import _make_ctx


@pytest.fixture
def loop_state() -> dict:
    return {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "feishu_p2p_synced_senders": set(),
    }


@pytest.mark.asyncio
async def test_handler_dispatches_slash_command(loop_state: dict) -> None:
    """以 / 开头的消息走 dispatch_command 并返回捕获结果。"""
    router = ChannelRouter()
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx
    ctx.feishu = MagicMock()
    ctx.feishu.get_config = MagicMock(return_value=MagicMock())

    with patch(
        "miniagent.engine.command_dispatch.dispatch_command",
        new_callable=AsyncMock,
        return_value="命令输出",
    ) as mock_dispatch:
        handler, _ = create_feishu_handler(loop_state, ctx, [True])
        inbound = FeishuInboundText(
            text="/help",
            chat_id="oc_cmd123",
            sender_id="ou_x",
            chat_type="group",
            message_id="msg_cmd",
        )
        result = await handler(inbound)

    assert result == "命令输出"
    mock_dispatch.assert_awaited_once()
    assert mock_dispatch.await_args.kwargs["capture"] is True


@pytest.mark.asyncio
async def test_p2p_auto_bind_on_first_message(loop_state: dict) -> None:
    """私聊首条消息自动绑定到 active_session_id。"""
    router = ChannelRouter()
    router.bind(ChannelRouter.CLI_CHANNEL, "default")
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx
    ctx.feishu = MagicMock()
    ctx.feishu.get_config = MagicMock(return_value=MagicMock())
    ctx.engine.run_agent_with_thinking = AsyncMock(return_value="ok")  # type: ignore[method-assign]

    handler, _ = create_feishu_handler(loop_state, ctx, [True])
    p2p_ch = f"{ChannelRouter.FEISHU_P2P_PREFIX}ou_bind_me"

    with patch("miniagent.engine.feishu_handler._send_feishu_agent_reply", new_callable=AsyncMock):
        inbound = FeishuInboundText(
            text="你好",
            chat_id="ou_bind_me",
            sender_id="ou_bind_me",
            chat_type="p2p",
            message_id="msg_p2p",
        )
        await handler(inbound)

    assert router.is_bound(p2p_ch)
    assert router.resolve(p2p_ch) == "default"
    assert "ou_bind_me" in loop_state["feishu_p2p_synced_senders"]


@pytest.mark.asyncio
async def test_media_handler_save_only_without_agent(loop_state: dict) -> None:
    """feishu.media.run_agent=false 时仅保存并返回成功提示。"""
    router = ChannelRouter()
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx

    class _StubSession:
        workspace_path = "/tmp/ws"

    class _StubSM:
        def get_or_create(self, _sk: str, _opts: object) -> _StubSession:
            return _StubSession()

    loop_state["session_manager"] = _StubSM()

    _, media_handler = create_feishu_handler(loop_state, ctx, [True])

    async def _fake_download(*_a: object, **_k: object) -> tuple[bytes, str]:
        return b"data", "doc.txt"

    with patch(
        "miniagent.engine.feishu_handler.get_config",
        side_effect=lambda key, default=None: False if key == "feishu.media.run_agent" else default,
    ), patch(
        "miniagent.feishu.resource_io.download_message_resource", _fake_download
    ), patch("builtins.open", create=True), patch(
        "miniagent.engine.feishu_handler.os.makedirs"
    ), patch(
        "miniagent.engine.feishu_handler.os.path.relpath", return_value="feishu_incoming/doc.txt"
    ), patch(
        "miniagent.memory.store.add_file_to_memory", new_callable=AsyncMock
    ):
        result = await media_handler(
            MagicMock(app_id="a", app_secret="s"),
            "msg_file",
            "oc_file1",
            "ou_x",
            "group",
            "file",
            "fk1",
            "doc.txt",
            "file",
        )

    assert result == f"{SUCCESS_PREFIX} 已保存到会话文件区: feishu_incoming/doc.txt"


@pytest.mark.asyncio
async def test_handler_returns_text_when_send_reply_raises(loop_state: dict) -> None:
    """卡片发送异常时 handler 回退返回正文供 poll_server 作 text 回复。"""
    router = ChannelRouter()
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx
    ctx.feishu = MagicMock()
    ctx.feishu.get_config = MagicMock(return_value=MagicMock())
    ctx.engine.run_agent_with_thinking = AsyncMock(return_value="Agent 正文")  # type: ignore[method-assign]

    handler, _ = create_feishu_handler(loop_state, ctx, [True])

    with patch(
        "miniagent.engine.feishu_handler._send_feishu_agent_reply",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network down"),
    ):
        inbound = FeishuInboundText(
            text="问题",
            chat_id="oc_fail1",
            sender_id="ou_x",
            chat_type="group",
            message_id="msg_fail",
        )
        result = await handler(inbound)

    assert result == "Agent 正文"
