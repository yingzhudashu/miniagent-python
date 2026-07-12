"""飞书 handler CLI 镜像门控测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.application.messaging import (
    ChannelRegistry,
    OrderedOutboundDispatcher,
)
from miniagent.bootstrap.application import ApplicationContainer
from miniagent.contracts import OutboundEventKind
from miniagent.engine.cli_outbound import build_cli_thinking_event
from miniagent.engine.engine import UnifiedEngine
from miniagent.engine.feishu_handler import create_feishu_handler
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.feishu.types import FeishuInboundText
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
from tests.channel_helpers import FunctionChannelAdapter
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)
from tests.test_startup import _make_memory_bundle


def _make_ctx(router: ChannelRouter) -> ApplicationContainer:
    mq = MessageQueueManager()
    ms, al, ki, mc = _make_memory_bundle()
    return ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=router,
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory=make_memory_runtime(
            store=ms,
            activity_log=al,
            keyword_index=ki,
            context=mc,
        ),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
    )


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
async def test_background_group_does_not_write_cli_user_or_reply(loop_state: dict) -> None:
    """一般模式下后台群不入 CLI：user/reply 块均不写入。"""
    router = ChannelRouter()
    router.bind(ChannelRouter.CLI_CHANNEL, "default")
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx

    append_calls: list[tuple[str, str]] = []

    def _append(style: str, text: str = "") -> None:
        append_calls.append((style, text))

    ctx.cli_transcript_append = _append
    ctx.cli_transcript_coordinator = CliTranscriptCoordinator(_append, None, parallel_sessions=True)

    engine = ctx.engine
    engine.run_agent_with_thinking = AsyncMock(return_value="后台回复")  # type: ignore[method-assign]

    handler, _ = create_feishu_handler(loop_state, ctx, [True])

    inbound = FeishuInboundText(
        text="后台群问题",
        chat_id="oc_bg123456",
        sender_id="ou_x",
        chat_type="group",
        message_id="msg1",
    )
    with patch("miniagent.engine.feishu_handler.format_cli_user_block") as mock_user, patch(
        "miniagent.engine.feishu_handler.format_cli_reply_block"
    ) as mock_reply:
        await handler(inbound)
        mock_user.assert_not_called()
        mock_reply.assert_not_called()

    assert append_calls == []


@pytest.mark.asyncio
async def test_bound_group_mirrors_user_and_reply(loop_state: dict) -> None:
    """CLI 绑定群时 mirror 群写入 user/reply。"""
    router = ChannelRouter()
    router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_bind123")
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx

    append_calls: list[tuple[str, str]] = []

    def _append(style: str, text: str = "") -> None:
        append_calls.append((style, text))

    ctx.cli_transcript_append = _append
    ctx.cli_transcript_coordinator = CliTranscriptCoordinator(_append, None, parallel_sessions=True)

    ctx.engine.run_agent_with_thinking = AsyncMock(return_value="绑定群回复")  # type: ignore[method-assign]
    ctx.feishu = MagicMock()
    ctx.feishu.get_config = MagicMock(return_value=MagicMock())

    handler, _ = create_feishu_handler(loop_state, ctx, [True])

    inbound = FeishuInboundText(
        text="绑定群问题",
        chat_id="oc_bind123",
        sender_id="ou_x",
        chat_type="group",
        message_id="msg2",
    )
    with patch("miniagent.engine.feishu_handler.format_cli_user_block") as mock_user, patch(
        "miniagent.engine.feishu_handler.format_cli_reply_block"
    ) as mock_reply, patch(
        "miniagent.engine.feishu_handler._send_feishu_agent_reply", new_callable=AsyncMock
    ):
        await handler(inbound)
        mock_user.assert_called_once()
        mock_reply.assert_called_once()


@pytest.mark.asyncio
async def test_bound_group_drains_thinking_before_adapter_final(loop_state: dict) -> None:
    """飞书镜像须等待标准思考事件后再经同一 CLI adapter 输出结论。"""
    router = ChannelRouter()
    router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_ordered")
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx

    append_calls: list[tuple[str, str]] = []

    def _append(style: str, text: str = "") -> None:
        append_calls.append((style, text))

    ctx.cli_transcript_append = _append
    ctx.cli_transcript_coordinator = CliTranscriptCoordinator(
        _append, None, parallel_sessions=True
    )
    delivered: list[tuple[OutboundEventKind, str]] = []

    async def cli_sender(event) -> None:
        delivered.append((event.kind, event.content))

    ctx.outbound_channels = ChannelRegistry(
        [FunctionChannelAdapter("cli", cli_sender)]
    )
    dispatcher = OrderedOutboundDispatcher(ctx.outbound_channels)
    ctx.cli_outbound_dispatcher = dispatcher

    async def run_agent(*_args, **_kwargs) -> str:
        dispatcher.publish(
            build_cli_thinking_event(
                "思考片段",
                "feishu:oc_ordered",
                interface="tui",
                fragment_kind="chunk",
            )
        )
        return "最终回复"

    ctx.engine.run_agent_with_thinking = run_agent  # type: ignore[method-assign]
    ctx.feishu = MagicMock()
    ctx.feishu.get_config = MagicMock(return_value=MagicMock())
    handler, _ = create_feishu_handler(loop_state, ctx, [True])
    inbound = FeishuInboundText(
        text="顺序问题",
        chat_id="oc_ordered",
        sender_id="ou_x",
        chat_type="group",
        message_id="msg_ordered",
    )

    with patch("miniagent.engine.feishu_handler.format_cli_user_block"), patch(
        "miniagent.engine.feishu_handler.format_cli_reply_block"
    ) as direct_reply, patch(
        "miniagent.engine.feishu_handler._send_feishu_agent_reply",
        new_callable=AsyncMock,
    ):
        await handler(inbound)

    assert delivered == [
        (OutboundEventKind.THINKING_DELTA, "思考片段"),
        (OutboundEventKind.FINAL, "最终回复"),
    ]
    direct_reply.assert_not_called()


@pytest.mark.asyncio
async def test_media_handler_mirror_writes_user_block(
    loop_state: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """media_handler 在 mirror 且 run_agent 时写入 user/reply 块。"""
    monkeypatch.setenv("MINIAGENT_CONFIG", "")

    router = ChannelRouter()
    router.bind(ChannelRouter.CLI_CHANNEL, "feishu:oc_media1")
    ctx = _make_ctx(router)
    loop_state["runtime_ctx"] = ctx

    append_calls: list[tuple[str, str]] = []

    def _append(style: str, text: str = "") -> None:
        append_calls.append((style, text))

    ctx.cli_transcript_append = _append
    ctx.cli_transcript_coordinator = CliTranscriptCoordinator(_append, None, parallel_sessions=True)
    ctx.engine.run_agent_with_thinking = AsyncMock(return_value="媒体回复")  # type: ignore[method-assign]
    ctx.feishu = MagicMock()
    ctx.feishu.get_config = MagicMock(return_value=MagicMock())

    class _StubSession:
        workspace_path = "/tmp/ws"

    class _StubSM:
        def get_or_create(self, _sk: str, _opts: object) -> _StubSession:
            return _StubSession()

    loop_state["session_manager"] = _StubSM()

    _, media_handler = create_feishu_handler(loop_state, ctx, [True])

    async def _fake_download(*_a: object, **_k: object) -> tuple[bytes, str]:
        return b"png", "img.png"

    with patch(
        "miniagent.engine.feishu_handler.get_config",
        side_effect=lambda key, default=None: True if key == "feishu.media.run_agent" else default,
    ), patch(
        "miniagent.feishu.resource_io.download_message_resource", _fake_download
    ), patch("builtins.open", create=True), patch(
        "miniagent.engine.feishu_handler.os.makedirs"
    ), patch(
        "miniagent.engine.feishu_handler.os.path.relpath", return_value="feishu_incoming/img.png"
    ), patch(
        "miniagent.memory.store.add_file_to_memory", new_callable=AsyncMock
    ), patch(
        "miniagent.engine.feishu_handler.format_cli_user_block"
    ) as mock_user, patch(
        "miniagent.engine.feishu_handler.format_cli_reply_block"
    ) as mock_reply:
        await media_handler(
            MagicMock(app_id="a", app_secret="s"),
            "msg_m1",
            "oc_media1",
            "ou_x",
            "group",
            "image",
            "fk1",
            "img.png",
            "image",
        )
        mock_user.assert_called_once()
        mock_reply.assert_called_once()
