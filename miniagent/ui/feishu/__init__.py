"""Feishu UI transport contracts without Agent or Assistant business logic."""

from miniagent.ui.feishu.inbound import (
    FEISHU_CHANNEL,
    build_feishu_inbound_message,
    build_feishu_media_inbound_message,
)
from miniagent.ui.feishu.outbound import (
    FeishuChannelAdapter,
    UnsupportedFeishuEventError,
    build_feishu_final_event,
    build_feishu_reply_event,
)
from miniagent.ui.feishu.types import FeishuConfig, FeishuInboundText

__all__ = [
    "FEISHU_CHANNEL",
    "FeishuChannelAdapter",
    "FeishuConfig",
    "FeishuInboundText",
    "UnsupportedFeishuEventError",
    "build_feishu_final_event",
    "build_feishu_inbound_message",
    "build_feishu_media_inbound_message",
    "build_feishu_reply_event",
]
