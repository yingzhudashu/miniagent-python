"""飞书轮询入口的离线 SDK 回调、路由与清理契约测试。"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.feishu import poll_server
from miniagent.assistant.feishu.types import FeishuConfig


class _Deduplicator:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.released: list[str] = []
        self.abandoned: list[str] = []

    def try_begin_processing(self, message_id: str) -> bool:
        if message_id in self.claimed:
            return False
        self.claimed.add(message_id)
        return True

    def release_processing(self, message_id: str) -> None:
        self.released.append(message_id)

    def abandon_processing_claim(self, message_id: str) -> None:
        self.abandoned.append(message_id)


class _RuntimeState:
    def __init__(self) -> None:
        self.client = None
        self.app_id = None
        self.shutdown_event = None
        self.callback_tasks: set[asyncio.Task] = set()
        self.deduplicator = _Deduplicator()
        self.card_actions = SimpleNamespace(should_skip=lambda _key: False)
        self.confirmation_engine = None
        self.channel_router = None
        self.ws_health = SimpleNamespace(
            touch_inbound=lambda: None,
            last_session_end=lambda: ("test", None),
        )
        self.debouncer = SimpleNamespace(schedule=self._schedule)
        self.reset_calls = 0

    async def _schedule(self, inbound, *, debounce_ms, on_flush) -> None:
        assert debounce_ms == 0
        await on_flush(inbound, [inbound.message_id])

    def spawn_callback_task(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.callback_tasks.add(task)
        task.add_done_callback(self.callback_tasks.discard)
        return task

    async def reset(self) -> None:
        self.reset_calls += 1
        tasks = list(self.callback_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.client is not None:
            await self.client._disconnect()
        self.client = None
        self.app_id = None
        self.shutdown_event = None


class _Queue:
    def __init__(self) -> None:
        self.chat_ids: list[str] = []

    async def dispatch(self, chat_id: str, awaitable) -> None:
        self.chat_ids.append(chat_id)
        await awaitable


class _Builder:
    def __init__(self) -> None:
        self.message_callback = None
        self.card_callback = None

    def register_p2_im_message_receive_v1(self, callback):
        self.message_callback = callback
        return self

    def register_p2_card_action_trigger(self, callback):
        self.card_callback = callback
        return self

    def build(self):
        return self


def _message_event(
    message_id: str,
    *,
    message_type: str = "text",
    content: str | None = None,
    create_time=0,
):
    message = SimpleNamespace(
        message_id=message_id,
        message_type=message_type,
        content=content if content is not None else json.dumps({"text": "hello"}),
        create_time=create_time,
        chat_id="chat-1",
        chat_type="p2p",
        root_id="root-1",
        parent_id="parent-1",
        thread_id="thread-1",
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id="user-1"))
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


def test_confirmation_channel_resolution_fallbacks() -> None:
    state = _RuntimeState()
    assert poll_server._resolve_feishu_confirmation_channel(
        state, "chat", "sender"
    ) is None

    fallback = object()
    state.confirmation_engine = SimpleNamespace(confirmation_channel=fallback)
    state.channel_router = SimpleNamespace(
        resolve_feishu_message=lambda *_args: (_ for _ in ()).throw(RuntimeError("bad route"))
    )
    assert (
        poll_server._resolve_feishu_confirmation_channel(state, "chat", "sender")
        is fallback
    )


def _install_fake_sdk(monkeypatch, state: _RuntimeState, drive_callbacks):
    import miniagent.assistant.feishu.ws_client as ws_client_module
    import miniagent.assistant.feishu.ws_health as ws_health_module

    dispatcher_module = importlib.import_module("lark_oapi.event.dispatcher_handler")
    debounce_module = importlib.import_module("miniagent.assistant.feishu.message_debounce")
    builder = _Builder()
    monkeypatch.setattr(
        dispatcher_module.EventDispatcherHandler,
        "builder",
        staticmethod(lambda *_args: builder),
    )

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.event_handler = kwargs["event_handler"]
            self.receive_task = None
            self.disconnected = False

        async def _connect(self) -> None:
            return None

        async def _ping_loop(self) -> None:
            await asyncio.Event().wait()

        async def _disconnect(self) -> None:
            self.disconnected = True

    monkeypatch.setattr(ws_client_module, "FeishuWsClient", FakeClient)
    monkeypatch.setattr(debounce_module, "feishu_message_debounce_ms", lambda: 0)

    async def supervise(client, **_kwargs) -> None:
        await drive_callbacks(client.event_handler)
        while state.callback_tasks:
            await asyncio.gather(*list(state.callback_tasks), return_exceptions=True)

    monkeypatch.setattr(ws_health_module, "supervise_feishu_ws_session", supervise)
    return builder


@pytest.mark.asyncio
async def test_poll_server_routes_text_media_and_ignored_messages(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    replies: list[tuple[str, str]] = []
    handled: list[str] = []
    media_calls: list[tuple[str, str]] = []

    async def send_reply(_config, chat_id, text, **_kwargs) -> None:
        replies.append((chat_id, text))

    async def message_handler(inbound) -> str:
        handled.append(inbound.text)
        return f"reply:{inbound.text}"

    async def media_handler(
        _config,
        _message_id,
        _chat_id,
        _sender_id,
        _chat_type,
        message_type,
        file_key,
        *_args,
    ) -> str:
        media_calls.append((message_type, file_key))
        return "media-ok"

    monkeypatch.setattr(poll_server, "_send_reply", send_reply)
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {
            "feishu.message_debounce_ms": 0,
            "feishu.max_message_age": 600,
            "feishu.card_action_router": False,
            "feishu.media.silent_reply": False,
        }.get(key, default),
    )

    async def drive(handler) -> None:
        handler.message_callback(_message_event("text-1"))
        handler.message_callback(_message_event("text-1"))
        handler.message_callback(_message_event("blank", content=json.dumps({"text": ""})))
        handler.message_callback(
            _message_event("file-1", message_type="file", content=json.dumps({"file_key": "fk"}))
        )
        handler.message_callback(
            _message_event("image-bad", message_type="image", content="not-json")
        )
        handler.message_callback(_message_event("unsupported", message_type="audio"))

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        message_handler,
        runtime_state=state,
        message_queue=queue,
        media_handler=media_handler,
    )

    assert handled == ["hello"]
    assert media_calls == [("file", "fk")]
    assert ("chat-1", "reply:hello") in replies
    assert ("chat-1", "media-ok") in replies
    assert set(state.deduplicator.released) >= {"text-1", "blank", "file-1", "image-bad", "unsupported"}
    assert state.reset_calls == 1


@pytest.mark.asyncio
async def test_poll_server_handles_missing_and_malformed_message_fields(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {
            "feishu.card_action_router": False,
            "feishu.max_message_age": 600,
        }.get(key, default),
    )

    async def drive(handler) -> None:
        handler.message_callback(SimpleNamespace(event=SimpleNamespace(message=None, sender=None)))
        handler.message_callback(_message_event(""))
        handler.message_callback(_message_event("bad-time", create_time="not-a-time"))
        handler.message_callback(
            _message_event("bad-text", message_type="text", content="not-json")
        )

    _install_fake_sdk(monkeypatch, state, drive)

    async def no_reply(_inbound) -> str:
        return ""

    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        no_reply,
        runtime_state=state,
        message_queue=queue,
    )
    assert set(state.deduplicator.released) >= {"bad-time", "bad-text"}


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_app", ["app", "other-app"])
async def test_poll_server_resets_residual_client(monkeypatch, existing_app) -> None:
    state = _RuntimeState()
    residual = SimpleNamespace(_disconnect=AsyncMock())
    state.client = residual
    state.app_id = existing_app
    queue = _Queue()
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": False}.get(key, default),
    )

    async def drive(_handler) -> None:
        return None

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        lambda _inbound: None,
        runtime_state=state,
        message_queue=queue,
    )
    assert state.reset_calls == 2
    residual._disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_server_abandons_failed_text_and_media_claims(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.message_debounce_ms": 0, "feishu.card_action_router": False}.get(
            key, default
        ),
    )

    async def failed_handler(_inbound) -> str:
        raise RuntimeError("handler failed")

    async def failed_media(*_args) -> str:
        return "⚠️ download failed"

    async def drive(handler) -> None:
        handler.message_callback(_message_event("text-fail"))
        handler.message_callback(
            _message_event(
                "image-fail",
                message_type="image",
                content=json.dumps({"image_key": "ik"}),
            )
        )

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        failed_handler,
        runtime_state=state,
        message_queue=queue,
        media_handler=failed_media,
    )
    assert set(state.deduplicator.abandoned) == {"text-fail", "image-fail"}


@pytest.mark.asyncio
async def test_poll_server_routes_card_action(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    handled: list[str] = []
    replies: list[str] = []

    async def message_handler(inbound) -> str:
        handled.append(inbound.text)
        return "card reply"

    async def send_reply(_config, _chat_id, text, **_kwargs) -> None:
        replies.append(text)

    monkeypatch.setattr(poll_server, "_send_reply", send_reply)
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": True}.get(key, default),
    )

    async def drive(handler) -> None:
        event = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(value={"miniagent_text": "/status", "chat_id": "card-chat"}),
                context=None,
                operator=SimpleNamespace(open_id="operator"),
            )
        )
        response = handler.card_callback(event)
        assert response.toast.type == "info"

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        message_handler,
        runtime_state=state,
        message_queue=queue,
    )
    assert handled == ["/status"]
    assert replies == ["card reply"]
    assert queue.chat_ids == ["card-chat"]


@pytest.mark.asyncio
async def test_poll_server_card_action_validation_dedupe_and_context(monkeypatch) -> None:
    state = _RuntimeState()
    state.card_actions = SimpleNamespace(should_skip=lambda key: key == "duplicate")
    queue = _Queue()
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": True}.get(key, default),
    )

    async def drive(callbacks) -> None:
        assert callbacks.card_callback(SimpleNamespace(event=None)).toast.type == "error"
        missing = SimpleNamespace(
            event=SimpleNamespace(action=SimpleNamespace(value={}), context=None, operator=None)
        )
        assert callbacks.card_callback(missing).toast.type == "error"
        duplicate = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(
                    value={
                        "miniagent_text": "/status",
                        "dedupe_key": "duplicate",
                    }
                ),
                context=SimpleNamespace(open_chat_id="context-chat"),
                operator=SimpleNamespace(open_id="user"),
            )
        )
        response = callbacks.card_callback(duplicate)
        assert response.toast.type == "info"
        assert "重复" in response.toast.content

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        lambda _inbound: None,
        runtime_state=state,
        message_queue=queue,
    )


@pytest.mark.asyncio
async def test_poll_server_card_action_handler_failure_is_contained(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": True}.get(key, default),
    )

    async def failed(_inbound) -> str:
        raise RuntimeError("agent failed")

    async def drive(callbacks) -> None:
        event = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(
                    value={"miniagent_text": "hello", "chat_id": "chat"}
                ),
                context=None,
                operator=None,
            )
        )
        assert callbacks.card_callback(event).toast.type == "info"

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        failed,
        runtime_state=state,
        message_queue=queue,
    )


@pytest.mark.asyncio
async def test_poll_server_routes_post_media_and_expires_old_messages(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    media_keys = []
    replies = []

    async def media_handler(_cfg, _mid, _chat, _sender, _chat_type, _msg_type, key, *_args):
        media_keys.append(key)
        return f"saved:{key}"

    async def send_reply(_cfg, _chat, text, **_kwargs):
        replies.append(text)

    monkeypatch.setattr(poll_server, "_send_reply", send_reply)
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {
            "feishu.card_action_router": False,
            "feishu.max_message_age": 1,
            "feishu.media.silent_reply": False,
        }.get(key, default),
    )
    post = {
        "content": [
            [{"tag": "img", "image_key": "image-key"}],
            [{"tag": "media", "file_key": "file-key", "file_name": "a.pdf"}],
        ]
    }

    async def drive(handler) -> None:
        handler.message_callback(_message_event("old", create_time=1))
        handler.message_callback(
            _message_event("post", message_type="post", content=json.dumps(post))
        )

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        lambda _inbound: None,
        runtime_state=state,
        message_queue=queue,
        media_handler=media_handler,
    )
    assert media_keys == ["image-key", "file-key"]
    assert replies == ["saved:image-key\nsaved:file-key"]
    assert "old" in state.deduplicator.released


@pytest.mark.asyncio
async def test_poll_server_command_and_clarification_control_paths(monkeypatch) -> None:
    from miniagent.agent.types.confirmation import ConfirmationStage

    state = _RuntimeState()
    queue = _Queue()
    handled: list[str] = []
    responses = []
    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.CLARIFICATION),
        respond=responses.append,
    )
    state.confirmation_engine = SimpleNamespace(get_confirmation_channel=lambda _key: channel)
    state.channel_router = SimpleNamespace(resolve_feishu_message=lambda *_args: "session")
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": False}.get(key, default),
    )

    async def handler(inbound) -> str:
        handled.append(inbound.text)
        return ""

    async def drive(callbacks) -> None:
        callbacks.message_callback(
            _message_event("command", content=json.dumps({"text": "/status"}))
        )
        callbacks.message_callback(
            _message_event("clarify", content=json.dumps({"text": "the answer"}))
        )

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        handler,
        runtime_state=state,
        message_queue=queue,
    )
    assert handled == ["/status"]
    assert len(responses) == 1
    assert "clarify" in state.deduplicator.released


@pytest.mark.asyncio
async def test_poll_server_nonclarification_pending_uses_debouncer(monkeypatch) -> None:
    from miniagent.agent.types.confirmation import ConfirmationStage

    state = _RuntimeState()
    queue = _Queue()
    handled: list[str] = []
    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.PLAN),
    )
    state.confirmation_engine = SimpleNamespace(get_confirmation_channel=lambda _key: channel)
    state.channel_router = SimpleNamespace(resolve_feishu_message=lambda *_args: "session")
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": False}.get(key, default),
    )

    async def handler(inbound) -> str:
        handled.append(inbound.text)
        return ""

    async def drive(callbacks) -> None:
        callbacks.message_callback(_message_event("pending"))

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        handler,
        runtime_state=state,
        message_queue=queue,
    )
    assert handled == ["hello"]


@pytest.mark.asyncio
async def test_poll_server_media_silent_and_post_failure_paths(monkeypatch) -> None:
    state = _RuntimeState()
    queue = _Queue()
    sends = AsyncMock()
    monkeypatch.setattr(poll_server, "_send_reply", sends)
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {
            "feishu.card_action_router": False,
            "feishu.media.silent_reply": True,
        }.get(key, default),
    )

    async def media_handler(*args):
        if args[5] == "post":
            return "⚠️ failed"
        return "saved"

    post = {"content": [[{"tag": "img", "image_key": "ik"}]]}

    async def drive(callbacks) -> None:
        callbacks.message_callback(
            _message_event(
                "file-silent",
                message_type="file",
                content=json.dumps({"file_key": "fk"}),
            )
        )
        callbacks.message_callback(
            _message_event("post-fail", message_type="post", content=json.dumps(post))
        )
        callbacks.message_callback(
            _message_event("post-empty", message_type="post", content="{}")
        )

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        lambda _inbound: None,
        runtime_state=state,
        message_queue=queue,
        media_handler=media_handler,
    )
    sends.assert_not_awaited()
    assert "file-silent" in state.deduplicator.released
    assert "post-fail" in state.deduplicator.abandoned
    assert "post-empty" in state.deduplicator.released


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_type"),
    [("/confirm", "success"), ("/reject", "warning"), ("/adjust 更简洁", "success")],
)
async def test_poll_server_card_confirmation_actions(monkeypatch, text, expected_type) -> None:
    from miniagent.agent.types.confirmation import ConfirmationStage

    state = _RuntimeState()
    queue = _Queue()
    responses = []
    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.PLAN),
        respond=lambda result: responses.append(result),
    )
    state.confirmation_engine = SimpleNamespace(get_confirmation_channel=lambda _key: channel)
    state.channel_router = SimpleNamespace(resolve_feishu_message=lambda *_args: "session")
    monkeypatch.setattr(
        poll_server,
        "get_config",
        lambda key, default=None: {"feishu.card_action_router": True}.get(key, default),
    )

    async def drive(handler) -> None:
        event = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(value={"miniagent_text": text, "chat_id": "chat"}),
                context=None,
                operator=SimpleNamespace(open_id="user"),
            )
        )
        response = handler.card_callback(event)
        assert response.toast.type == expected_type

    _install_fake_sdk(monkeypatch, state, drive)
    await poll_server.start_feishu_poll_server(
        FeishuConfig("app", "secret"),
        lambda _inbound: None,
        runtime_state=state,
        message_queue=queue,
    )
    assert len(responses) == 1
