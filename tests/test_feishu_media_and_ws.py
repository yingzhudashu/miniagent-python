"""飞书媒体解析与实例连接状态清理测试。"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_parse_feishu_media_payload_file_and_image():
    from miniagent.feishu.poll_server import _parse_feishu_media_payload

    assert _parse_feishu_media_payload("file", '{"file_key":"fk1","file_name":"a.pdf"}') == (
        "file",
        "fk1",
        "a.pdf",
    )
    assert _parse_feishu_media_payload("image", '{"image_key":"ik9"}') == (
        "image",
        "ik9",
        "image",
    )
    assert _parse_feishu_media_payload("file", "{}") is None
    assert _parse_feishu_media_payload("file", "not-json") is None
    assert _parse_feishu_media_payload("file", '{"file_key":"fk2","name":"alternate.bin"}') == (
        "file",
        "fk2",
        "alternate.bin",
    )


@pytest.mark.asyncio
async def test_feishu_poll_state_reset_disconnects_and_clears():
    from miniagent.feishu.poll_server import FeishuPollState

    class _FakeClient:
        def __init__(self) -> None:
            self.disconnected = False

        async def _disconnect(self) -> None:
            self.disconnected = True

    c = _FakeClient()
    state = FeishuPollState()
    state.client = c
    state.app_id = "app_test"
    await state.reset()
    assert state.client is None
    assert state.app_id is None
    assert c.disconnected is True


@pytest.mark.asyncio
async def test_feishu_poll_state_reset_awaits_callback_tasks() -> None:
    from miniagent.feishu.poll_server import FeishuPollState

    state = FeishuPollState()
    cancelled = asyncio.Event()

    async def callback_work() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    state.spawn_callback_task(callback_work())
    await asyncio.sleep(0)
    await state.reset()

    assert cancelled.is_set()
    assert state.callback_tasks == set()


@pytest.mark.asyncio
async def test_feishu_poll_state_task_failures_shutdown_and_disconnect_errors() -> None:
    from miniagent.feishu.poll_state import FeishuPollState

    state = FeishuPollState()
    state.request_shutdown()
    state.shutdown_event = asyncio.Event()
    state.request_shutdown()
    assert state.shutdown_event.is_set()

    async def fail() -> None:
        raise RuntimeError("callback failed")

    state.spawn_callback_task(fail())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert state.callback_tasks == set()

    state.client = SimpleNamespace(_disconnect=AsyncMock(side_effect=RuntimeError("closed")))
    await state.reset()
    assert state.client is None


def test_feishu_poll_state_spawn_without_loop_closes_coroutine(monkeypatch) -> None:
    from miniagent.feishu.poll_state import FeishuPollState

    state = FeishuPollState()

    async def work() -> None:
        return None

    coroutine = work()
    monkeypatch.setattr(asyncio, "create_task", MagicMock(side_effect=RuntimeError("no loop")))
    with pytest.raises(RuntimeError):
        state.spawn_callback_task(coroutine)
    assert coroutine.cr_frame is None


def test_abandon_processing_claim_allows_retry_release_writes_disk_dedup(tmp_path):
    """失败路径应 abandon：同一 message_id 可再次 try_begin；release 后写入磁盘去重。"""
    from miniagent.feishu.feishu_dedup import FeishuDeduplicator

    mid = f"dedup-test-{uuid.uuid4().hex}"
    dedup = FeishuDeduplicator(str(tmp_path))
    assert dedup.try_begin_processing(mid)
    dedup.abandon_processing_claim(mid)
    assert dedup.try_begin_processing(mid)

    dedup.release_processing(mid)
    assert not dedup.try_begin_processing(mid)

def test_extract_post_media_items_recurses_img_and_media():
    from miniagent.feishu.poll_server import _extract_post_media_items

    payload = {
        "zh_cn": {
            "content": [
                [{"tag": "img", "image_key": "img_x"}],
                [{"tag": "media", "file_key": "f_y", "file_name": "a.bin"}],
            ]
        }
    }
    items = _extract_post_media_items(json.dumps(payload))
    keys = {(t, fk) for t, fk, _ in items}
    assert keys == {("image", "img_x"), ("file", "f_y")}


def test_media_parsers_cover_invalid_image_type_duplicates_and_depth() -> None:
    from miniagent.feishu.poll_state import (
        _extract_post_media_items,
        _feishu_media_reply_indicates_failure,
        _parse_feishu_media_payload,
    )

    assert _parse_feishu_media_payload("image", "{}") is None
    assert _parse_feishu_media_payload("audio", "{}") is None
    assert _parse_feishu_media_payload("image", None) is None
    payload = {
        "items": [
            {"tag": "img", "image_token": "same"},
            {"tag": "img", "image_key": "same"},
            {"tag": "media", "file_key": "file", "name": "name"},
        ]
    }
    assert len(_extract_post_media_items(json.dumps(payload))) == 2
    assert _extract_post_media_items("bad-json") == []
    deep: object = {"tag": "img", "image_key": "too-deep"}
    for _ in range(12):
        deep = [deep]
    assert _extract_post_media_items(json.dumps(deep)) == []
    assert not _feishu_media_reply_indicates_failure(None)
    assert _feishu_media_reply_indicates_failure("  ⚠️ failed")
