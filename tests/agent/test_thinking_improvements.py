"""ThinkingDisplay 改进回归：reset 完整性、buffer API、LRU、CLI 镜像策略。"""

from __future__ import annotations

import pytest

from miniagent.assistant.engine.thinking import ThinkingDisplay


def test_reset_counter_clears_feishu_cache_fields() -> None:
    """reset_counter 应清零 feishu_stream_llm_len 与卡片渲染缓存。"""
    td = ThinkingDisplay()
    state = td._get_state("sk")
    state.feishu_stream_llm_len = 42
    state.feishu_cached_card_key = ("a", "b", "c")
    state.feishu_cached_card_json = '{"x":1}'
    state.feishu_thinking_message_id = "om_mid"
    td.reset_counter("sk")
    assert state.feishu_stream_llm_len == 0
    assert state.feishu_cached_card_key is None
    assert state.feishu_cached_card_json is None
    assert state.feishu_thinking_message_id is None


@pytest.mark.asyncio
async def test_enable_buffer_collects_for_session_key() -> None:
    """enable_buffer 应对 session_key 对应 bucket 收集，而非孤立的 _default。"""
    td = ThinkingDisplay()
    td.enable_buffer("sess")
    await td.show("line one", session_key="sess", streaming=False, header="")
    await td.show("line two", session_key="sess", streaming=False, header="")
    assert td.get_buffered("sess") == "line one\nline two"
    assert td.get_buffered("") == ""


def test_get_state_lru_evicts_least_recently_used() -> None:
    """超过上限时驱逐最久未访问的 session，而非刚创建顺序的 FIFO。"""
    td = ThinkingDisplay()
    td._max_session_states = 3
    td._get_state("a")
    td._get_state("b")
    td._get_state("c")
    td._get_state("a")  # 刷新 a 的 LRU 顺序
    td._get_state("d")  # 应驱逐 b
    assert "a" in td._states
    assert "b" not in td._states
    assert "c" in td._states
    assert "d" in td._states


def test_should_emit_cli_pure_feishu_no_sink() -> None:
    """仅飞书、无 transcript sink 时不重复 CLI 打印。"""
    td = ThinkingDisplay()
    state = td._get_state("sk")
    state.feishu_send = lambda *a, **k: None  # type: ignore[assignment]
    state.feishu_chat_id = "oc_x"
    assert td._should_emit_cli(state) is False


def test_should_emit_cli_mirror_cli_false_with_sink() -> None:
    """有 sink 但 mirror_cli=False 时不镜像飞书思考到 transcript。"""
    td = ThinkingDisplay()
    td.set_output_sink(lambda *_a, **_k: None)
    state = td._get_state("sk")
    state.feishu_send = lambda *a, **k: None  # type: ignore[assignment]
    state.feishu_chat_id = "oc_x"
    state.feishu_mirror_cli = False
    assert td._should_emit_cli(state) is False


def test_should_emit_cli_mirror_cli_true_with_sink() -> None:
    td = ThinkingDisplay()
    td.set_output_sink(lambda *_a, **_k: None)
    state = td._get_state("sk")
    state.feishu_send = lambda *a, **k: None  # type: ignore[assignment]
    state.feishu_chat_id = "oc_x"
    state.feishu_mirror_cli = True
    assert td._should_emit_cli(state) is True


@pytest.mark.asyncio
async def test_mirror_cli_false_skips_transcript_output() -> None:
    """mirror_cli=False 时 show 不写入 output_sink。"""
    td = ThinkingDisplay()
    sink: list[str] = []

    def capture(text: str, kind: str = "chunk", **_kw: object) -> None:
        sink.append(text)

    td.set_output_sink(capture)

    async def feishu_send(*_a: object, **_k: object) -> None:
        pass

    td.enable_feishu("sk", "oc_x", feishu_send, mirror_cli=False)
    await td.show("secret thought", session_key="sk", streaming=False, header="[执行]")
    assert sink == []


def test_disable_buffer_clears_all_sessions_when_no_key() -> None:
    td = ThinkingDisplay()
    td.enable_buffer("a")
    td._get_state("a").buffer.append("x")
    td._get_state("b").buffer.append("y")
    td.disable_buffer()
    assert td._buffer_enabled is False
    assert td.get_buffered("a") == ""
    assert td.get_buffered("b") == ""
