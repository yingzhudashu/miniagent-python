"""CliTranscriptCoordinator 轮次连贯性测试。"""

from __future__ import annotations

import threading

from miniagent.assistant.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator


def test_single_turn_append_passthrough_immediately() -> None:
    """单 active turn 时 append 立即透传（流式不缓冲）。"""
    output: list[tuple[str, str]] = []

    def append(style: str, text: str = "") -> None:
        output.append((style, text))

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)
    coord.begin_turn("session_a", source="cli")
    coord.append("session_a", "class:chunk", "hello")
    assert output == [("class:chunk", "hello")]
    coord.end_turn("session_a")


def test_parallel_turns_flush_in_begin_order_not_interleaved() -> None:
    """两 session 并行时缓冲轮次按 begin 顺序整块 flush，不交错。"""
    output: list[str] = []
    lock = threading.Lock()

    def append(style: str, text: str = "") -> None:
        with lock:
            output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)

    coord.begin_turn("A", source="feishu")
    coord.append("A", "s", "A1\n")
    coord.append("A", "s", "A2\n")

    coord.begin_turn("B", source="feishu")
    coord.append("B", "s", "B1\n")
    coord.append("B", "s", "B2\n")

    assert output == ["A1\n", "A2\n"]
    assert not any(t.startswith("B") for t in output)

    coord.end_turn("A")
    assert output == ["A1\n", "A2\n"]

    coord.end_turn("B")
    assert output == ["A1\n", "A2\n", "B1\n", "B2\n"]


def test_defer_buffers_thinking_until_end_turn() -> None:
    """缓冲轮次的 defer 在 end_turn 时一次性执行。"""
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)
    coord.begin_turn("live", source="cli")
    coord.append("live", "s", "LIVE\n")

    coord.begin_turn("buf", source="feishu")
    assert not coord.is_live("buf")
    coord.defer("buf", lambda: output.append("THINK\n"))
    coord.append("buf", "s", "REPLY\n")

    coord.end_turn("live")
    assert output == ["LIVE\n"]

    coord.end_turn("buf")
    assert output == ["LIVE\n", "THINK\n", "REPLY\n"]


def test_parallel_sessions_false_always_passthrough() -> None:
    """parallel_sessions=false 时退化为直写。"""
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=False)
    coord.begin_turn("A", source="cli")
    coord.begin_turn("B", source="feishu")
    coord.append("A", "s", "a")
    coord.append("B", "s", "b")
    assert output == ["a", "b"]


def test_unregistered_session_dropped_when_other_turn_active() -> None:
    """有其他 active turn 时，未登记 session 的 append/defer 为 no-op。"""
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)
    coord.begin_turn("A", source="cli")
    coord.append("A", "s", "A\n")
    coord.append("stray", "s", "STRAY\n")
    coord.defer("stray", lambda: output.append("THINK\n"))
    assert output == ["A\n"]
    assert not coord.is_live("stray")


def test_begin_turn_idempotent() -> None:
    """同 session 重复 begin_turn 不覆盖已有轮次。"""
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(append, None, parallel_sessions=True)
    coord.begin_turn("A", source="cli")
    coord.begin_turn("A", source="cli")
    assert coord.active_turn_count == 1
    coord.append("A", "s", "ok")
    assert output == ["ok"]


def test_on_turn_end_callback() -> None:
    """end_turn 触发 on_turn_end 回调。"""
    ended: list[str] = []
    output: list[str] = []

    def append(style: str, text: str = "") -> None:
        output.append(text)

    coord = CliTranscriptCoordinator(
        append, None, parallel_sessions=True, on_turn_end=lambda sk: ended.append(sk)
    )
    coord.begin_turn("sess_x", source="cli")
    coord.end_turn("sess_x")
    assert ended == ["sess_x"]
