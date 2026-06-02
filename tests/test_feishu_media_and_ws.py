"""飞书媒体解析、WS 单例清理等单元测试。"""

from __future__ import annotations

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
    assert _parse_feishu_media_payload("file", '{"file_key":"fk2","name":"legacy.bin"}') == (
        "file",
        "fk2",
        "legacy.bin",
    )


@pytest.mark.asyncio
async def test_reset_feishu_ws_singleton_disconnects_and_clears():
    import miniagent.feishu.poll_server as ps

    class _FakeClient:
        def __init__(self) -> None:
            self.disconnected = False

        async def _disconnect(self) -> None:
            self.disconnected = True

    c = _FakeClient()
    ps._singleton_client = c
    ps._singleton_app_id = "app_test"
    await ps.reset_feishu_ws_singleton()
    assert ps._singleton_client is None
    assert ps._singleton_app_id is None
    assert c.disconnected is True


def test_abandon_processing_claim_allows_retry_release_writes_disk_dedup():
    """失败路径应 abandon：同一 message_id 可再次 try_begin；release 后写入磁盘去重。"""
    import miniagent.feishu.feishu_dedup as dedup

    mid = f"dedup-test-{uuid.uuid4().hex}"
    assert dedup.try_begin_processing(mid)
    key = dedup._resolve_dedup_key(mid)
    dedup.abandon_processing_claim(mid)
    assert key not in dedup._disk_dedup
    assert dedup.try_begin_processing(mid)

    dedup.release_processing(mid)
    assert key in dedup._disk_dedup
    assert not dedup.try_begin_processing(mid)

    dedup._disk_dedup.pop(key, None)


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
