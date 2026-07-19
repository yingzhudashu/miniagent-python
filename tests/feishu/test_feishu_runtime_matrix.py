"""飞书 poll/handler 对象化后的错误、降级与生命周期矩阵。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import feishu_handler
from miniagent.assistant.feishu import poll_server
from miniagent.ui.feishu.types import FeishuConfig, FeishuInboundText


class _Dedup:
    def __init__(self) -> None:
        self.released = []
        self.abandoned = []

    def try_begin_processing(self, _message_id):
        return True

    def release_processing(self, message_id):
        self.released.append(message_id)

    def abandon_processing_claim(self, message_id):
        self.abandoned.append(message_id)


def _poll_callbacks(message_handler=None, media_handler=None):
    if message_handler is None:
        message_handler = AsyncMock(return_value="")
    dedup = _Dedup()
    state = SimpleNamespace(
        ws_health=SimpleNamespace(touch_inbound=MagicMock()),
        deduplicator=dedup,
        confirmation_engine=None,
        channel_router=None,
        debouncer=SimpleNamespace(schedule=AsyncMock()),
        card_actions=SimpleNamespace(should_skip=lambda _key: False),
        spawn_callback_task=lambda coro: asyncio.create_task(coro),
    )
    queue = SimpleNamespace(dispatch=lambda _chat, job: job)
    callbacks = poll_server._FeishuPollCallbacks(
        FeishuConfig("app", "secret"), message_handler, state, queue, media_handler
    )
    return callbacks, state


def _event(message_id="m1", message_type="text", content='{"text":"hello"}'):
    message = SimpleNamespace(
        message_id=message_id,
        create_time=0,
        chat_id="chat",
        chat_type="group",
        message_type=message_type,
        content=content,
        root_id="",
        parent_id="",
        thread_id="",
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="user"))
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


def test_poll_callback_malformed_and_expired(monkeypatch) -> None:
    callbacks, state = _poll_callbacks()
    callbacks.on_message_receive(SimpleNamespace(event=SimpleNamespace(message=None)))
    callbacks.on_message_receive(_event(message_id=""))
    assert callbacks._create_time(SimpleNamespace(create_time="bad")) == 0
    assert callbacks._create_time(SimpleNamespace(create_time="1700000000123")) == 1_700_000_000
    assert callbacks._extract_text("text", "not-json") == "not-json"
    assert callbacks._extract_text("file", "x") == ""

    monkeypatch.setattr(poll_server.time, "time", lambda: 1000)
    monkeypatch.setattr(
        poll_server, "get_config", lambda key, default=None: 1 if key == "feishu.max_message_age" else default
    )
    event = _event("old")
    event.event.message.create_time = 1
    callbacks.on_message_receive(event)
    assert "old" in state.deduplicator.released


@pytest.mark.asyncio
async def test_poll_jobs_release_and_abandon(monkeypatch) -> None:
    replies = AsyncMock()
    monkeypatch.setattr(poll_server, "_send_reply", replies)
    handler = AsyncMock(return_value="reply")
    callbacks, state = _poll_callbacks(handler)
    inbound = FeishuInboundText("hello", "chat", "user", "group", message_id="m1")
    await callbacks._text_job(inbound, ["m1", "m2"])
    assert state.deduplicator.released == ["m1", "m2"]
    replies.assert_awaited_once()

    callbacks.message_handler = AsyncMock(side_effect=RuntimeError("fail"))
    await callbacks._text_job(inbound, ["m3"])
    assert state.deduplicator.abandoned == ["m3"]


@pytest.mark.asyncio
async def test_media_and_post_jobs_matrix(monkeypatch) -> None:
    context = {
        "message_id": "media",
        "chat_id": "chat",
        "sender_id": "user",
        "chat_type": "group",
    }
    media = AsyncMock(return_value="saved")
    callbacks, state = _poll_callbacks(media_handler=media)
    monkeypatch.setattr(poll_server, "get_config", lambda *_args, **_kwargs: True)
    await callbacks._media_job(context, "file", "file", "fk", "a.txt", None)
    assert state.deduplicator.released == ["media"]

    context["message_id"] = "post"
    media.return_value = "saved"
    await callbacks._post_job(context, [("image", "ik", "a.png")], None)
    assert "post" in state.deduplicator.released

    context["message_id"] = "bad"
    media.return_value = "⚠️ failed"
    await callbacks._post_job(context, [("image", "ik", "a.png")], None)
    assert "bad" in state.deduplicator.abandoned


@pytest.mark.asyncio
async def test_cleanup_ws_tasks_matrix() -> None:
    ping = asyncio.create_task(asyncio.Event().wait())
    receive = asyncio.create_task(asyncio.Event().wait())
    client = SimpleNamespace(receive_task=receive)
    await poll_server._cleanup_feishu_ws_tasks(client, ping)
    assert ping.cancelled() and receive.cancelled()
    await poll_server._cleanup_feishu_ws_tasks(SimpleNamespace(receive_task=None), None)


def _handler_runtime(tmp_path: Path):
    engine = SimpleNamespace(
        get_confirmation_channel=MagicMock(return_value=None),
        clear_last_reflection=MagicMock(),
    )
    channels = SimpleNamespace(
        get=lambda _name: object(), register=MagicMock(), send=AsyncMock()
    )
    router = SimpleNamespace(
        FEISHU_P2P_PREFIX="p2p:",
        is_bound=lambda _key: False,
        bind=MagicMock(),
        resolve_feishu_message=lambda *_args: "session",
    )
    memory = SimpleNamespace(store=object())
    ctx = SimpleNamespace(
        engine=engine,
        registry=object(),
        monitor=object(),
        channel_router=router,
        outbound_channels=channels,
        feishu=SimpleNamespace(get_config=lambda: SimpleNamespace()),
        memory=memory,
        knowledge_registry=object(),
        clawhub=None,
        llm_gateway=None,
        cli_transcript_coordinator=None,
        cli_transcript_append=None,
        cli_transcript_append_ansi=None,
        cli_outbound_dispatcher=None,
    )
    state = {"active_session_id": "active"}
    runtime = feishu_handler._FeishuHandlerRuntime(state, ctx, [True])
    return runtime, ctx


@pytest.mark.asyncio
async def test_handler_command_and_clarification_matrix(tmp_path, monkeypatch) -> None:
    runtime, ctx = _handler_runtime(tmp_path)
    import miniagent.assistant.engine.command_dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "dispatch_command", AsyncMock(return_value="status"))
    inbound = FeishuInboundText("/status", "chat", "user", "group", message_id="m")
    handled, result = await runtime._handle_command(inbound)
    assert handled and result == ""
    ctx.outbound_channels.send.assert_awaited_once()

    from miniagent.agent.types.confirmation import ConfirmationStage

    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.CLARIFICATION),
        respond=MagicMock(),
    )
    assert runtime._respond_clarification(channel, "answer") is True
    channel.respond.assert_called_once()
    assert runtime._respond_clarification(None, "answer") is False


@pytest.mark.asyncio
async def test_media_download_prompt_and_validation(tmp_path, monkeypatch) -> None:
    runtime, ctx = _handler_runtime(tmp_path)
    from miniagent.assistant.feishu import resource_io

    monkeypatch.setattr(
        resource_io,
        "download_message_resource",
        AsyncMock(return_value=(b"\x89PNG\r\n\x1a\n", "image.bin")),
    )
    config = SimpleNamespace(app_id="a", app_secret="s")
    path, relative, filename, data, mime = await runtime._download_media(
        config, str(tmp_path), "message", "key", "fallback", "image"
    )
    assert Path(path).is_file()
    assert relative and filename.endswith(".png") and data
    assert mime.startswith("image/")

    monkeypatch.setattr(feishu_handler, "get_config", lambda key, default=None: False)
    prompt = await runtime._media_prompt("image", path, relative)
    assert "已保存媒体" in prompt
    assert runtime._media_runs_agent() is False

    runtime.engine = None
    result = await runtime.media_handler(
        config, "m", "chat", "user", "group", "file", "key", "a", "file"
    )
    assert "引擎未初始化" in result
