"""飞书兼容层 ``create_feishu_server`` 构造与配置。"""

from __future__ import annotations

import inspect

from miniagent.feishu.server import create_feishu_server


def test_create_feishu_server_builds_feishu_config_with_verification_token() -> None:
    """``FeishuConfig`` 使用 ``verification_token`` 字段，避免 ``verify_token`` 误名导致 TypeError。"""
    start = create_feishu_server(
        "app_x",
        "sec_y",
        verify_token="vtok",
        encrypt_key="enc",
    )
    assert inspect.iscoroutinefunction(start)
