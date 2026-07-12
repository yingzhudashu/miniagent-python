"""飞书媒体解析与实例连接状态清理测试。"""

from __future__ import annotations

import asyncio
import json
import uuid

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
