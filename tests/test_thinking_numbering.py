"""ThinkingDisplay 编号系统 — CLI/飞书一致性验证。"""

from miniagent.assistant.engine.thinking import ThinkingDisplay


def test_next_turn_persistent_across_reset() -> None:
    """next_turn 持久递增，不随 reset_counter 清零。"""
    td = ThinkingDisplay()
    assert td.next_turn("") == 1
    assert td.next_turn("") == 2
    td.reset_counter("")
    assert td.next_turn("") == 3


def test_step_counter_resets_per_turn() -> None:
    """step_counter 在 reset_counter 后从零重新开始。"""
    td = ThinkingDisplay()
    assert td._next_step("") == 0
    assert td._next_step("") == 1
    td.reset_counter("")
    assert td._next_step("") == 0


def test_turn_number_per_session_isolation() -> None:
    """不同 session_key 的 turn_number 独立。"""
    td = ThinkingDisplay()
    assert td.next_turn("session_a") == 1
    assert td.next_turn("session_b") == 1
    assert td.next_turn("session_a") == 2
    assert td.next_turn("session_b") == 2


def test_step_counter_per_session_isolation() -> None:
    """不同 session_key 的 step_counter 独立。"""
    td = ThinkingDisplay()
    td._next_step("a")
    td._next_step("a")
    assert td._next_step("b") == 0  # b 从 0 开始


def test_reset_counter_clears_stream_state() -> None:
    """reset_counter 清除流式状态（stream_step/stream_header/stream_done）。"""
    td = ThinkingDisplay()
    td.reset_counter("")
    state = td._get_state("")
    state.stream_step = 5
    state.stream_header = "[执行]"
    state.stream_done = True
    td.reset_counter("")
    assert state.stream_step is None
    assert state.stream_header == ""
    assert state.stream_done is False


def test_reset_counter_preserves_turn_number() -> None:
    """reset_counter 不改变 turn_number。"""
    td = ThinkingDisplay()
    td.next_turn("")
    td.next_turn("")
    td.reset_counter("")
    state = td._get_state("")
    assert state.turn_number == 2
