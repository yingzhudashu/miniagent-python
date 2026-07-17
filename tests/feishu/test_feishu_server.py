"""飞书 ``start_feishu_poll_server`` 构造与配置验证。"""

from __future__ import annotations

import inspect

from miniagent.assistant.feishu.poll_server import start_feishu_poll_server


def test_start_feishu_poll_server_is_async_coroutine() -> None:
    """``start_feishu_poll_server`` 是异步协程函数，接受 FeishuConfig 配置。"""
    assert inspect.iscoroutinefunction(start_feishu_poll_server)
    # 签名检查：第一个参数应为 config: FeishuConfig
    sig = inspect.signature(start_feishu_poll_server)
    params = list(sig.parameters.keys())
    assert "config" in params
    assert "message_handler" in params
    assert "message_queue" in params
