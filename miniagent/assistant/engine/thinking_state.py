"""单会话思考流与飞书卡片 PATCH 状态。"""

from __future__ import annotations

from typing import Protocol


class OnFeishuSend(Protocol):
    """飞书思考卡片发送回调协议。"""

    async def __call__(
        self,
        chat_id: str,
        text: str,
        template: str,
        *,
        is_new_round: bool = False,
        streaming: bool = True,
        merge_tools: bool = False,
        finalize_only: bool = False,
    ) -> None: ...


class SessionThinkingState:
    """单会话 CLI 流式显示与飞书 PATCH 节流状态。"""

    __slots__ = (
        "step_counter", "buffer", "feishu_send", "feishu_chat_id", "feishu_session_key",
        "feishu_reply_to_message_id", "feishu_reply_in_thread", "feishu_mirror_cli",
        "stream_step", "stream_header", "stream_done", "stream_printed",
        "feishu_thinking_message_id", "feishu_stream_accumulated", "feishu_stream_llm_len",
        "feishu_cached_card_key", "feishu_cached_card_json", "feishu_last_patch_monotonic",
        "feishu_last_patched_char_len", "feishu_last_sent_card_json", "feishu_patch_budget",
        "feishu_tool_section_started", "feishu_pending_tool_lines", "feishu_pending_header",
        "turn_number", "_last_stream_full",
    )

    def __init__(self) -> None:
        """初始化所有显示、缓存和节流字段。"""
        self.step_counter = 0
        self.buffer: list[str] = []
        self.feishu_send: OnFeishuSend | None = None
        self.feishu_chat_id = ""
        self.feishu_session_key = ""
        self.feishu_reply_to_message_id: str | None = None
        self.feishu_reply_in_thread = False
        self.feishu_mirror_cli = True
        self.stream_step: int | None = None
        self.stream_header = ""
        self.stream_done = False
        self.stream_printed = 0
        self.feishu_thinking_message_id: str | None = None
        self.feishu_stream_accumulated = ""
        self.feishu_stream_llm_len = 0
        self.feishu_cached_card_key: tuple[str, str, str | None] | None = None
        self.feishu_cached_card_json: str | None = None
        self.feishu_last_patch_monotonic = 0.0
        self.feishu_last_patched_char_len = -1
        self.feishu_last_sent_card_json: str | None = None
        self.feishu_patch_budget = 0
        self.feishu_tool_section_started = False
        self.feishu_pending_tool_lines: list[str] = []
        self.feishu_pending_header = ""
        self.turn_number = 0
        self._last_stream_full = ""


def clear_feishu_stream_fields(state: SessionThinkingState) -> None:
    """清零飞书流式卡片字段，保留 CLI 与会话绑定信息。"""
    state.feishu_thinking_message_id = None
    state.feishu_stream_accumulated = ""
    state.feishu_stream_llm_len = 0
    state.feishu_cached_card_key = None
    state.feishu_cached_card_json = None
    state.feishu_last_patch_monotonic = 0.0
    state.feishu_last_patched_char_len = -1
    state.feishu_last_sent_card_json = None
    state.feishu_patch_budget = 0
    state.feishu_tool_section_started = False
    state.feishu_pending_tool_lines = []
    state.feishu_pending_header = ""


__all__ = ["OnFeishuSend", "SessionThinkingState", "clear_feishu_stream_fields"]
