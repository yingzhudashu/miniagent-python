"""Fallback CLI 并行显示隔离测试。"""

from __future__ import annotations

import pytest

from miniagent.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator


def test_fallback_coordinator_unregistered_thinking_dropped() -> None:
    """fallback 协调器：有其他 turn 时未登记 session 的 defer 不写。"""
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)

    def sink_inner(text: str, kind: str, session_key: str) -> None:
        sk = (session_key or "").strip() or "default"
        if coord.is_live(sk):
            output.append(text)
        else:
            coord.defer(sk, lambda: output.append(text))

    coord.begin_turn("cli_sess", source="cli")
    sink_inner("live-think", "chunk", "cli_sess")
    sink_inner("stray-think", "chunk", "other_sess")
    assert output == ["live-think"]
    coord.end_turn("cli_sess")


@pytest.mark.asyncio
async def test_thinking_sink_session_key_via_coordinator_defer() -> None:
    """经 coordinator 路由的 thinking 在缓冲轮次结束后 flush。"""
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)
    coord.begin_turn("A", source="cli")
    coord.begin_turn("B", source="feishu")

    def emit(sk: str, text: str) -> None:
        if coord.is_live(sk):
            output.append(text)
        else:
            coord.defer(sk, lambda t=text: output.append(t))

    emit("A", "A-think")
    emit("B", "B-think")
    assert output == ["A-think"]
    coord.end_turn("A")
    coord.end_turn("B")
    assert output == ["A-think", "B-think"]
