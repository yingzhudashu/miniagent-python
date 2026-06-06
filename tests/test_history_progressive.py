"""渐进式会话历史压缩与单次归档/trim 语义。"""

from __future__ import annotations

import pytest

from miniagent.memory.history_archive import maybe_archive_old_turns, trim_history_tail_by_turns
from miniagent.memory.history_progressive import (
    TOOL_OUTPUT_REDACTED_PLACEHOLDER,
    apply_one_progressive_disk_step,
    compress_first_step_span_in_text,
    redact_first_tool_output_in_text,
    run_session_history_maintenance,
    strip_thinking_to_turn_summary,
)
from tests.config_helpers import install_test_config
from tests.history_helpers import history_turn as _turn


def test_maybe_archive_at_most_one_turn_per_call(tmp_path):
    install_test_config(
        tmp_path,
        {
            "paths": {"state_dir": str(tmp_path)},
            "memory": {"history_max_messages": 4},
        },
    )
    hist = _turn("u1", "a1") + _turn("u2", "a2") + _turn("u3", "a3")
    assert len(hist) == 6
    assert maybe_archive_old_turns("sess", hist) is True
    assert len(hist) == 5  # 一轮 2 条换 1 条锚点


def test_trim_at_most_one_turn_per_call(tmp_path):
    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    hist: list[dict] = []
    for i in range(5):
        hist.extend(_turn(f"u{i}", f"a{i}"))
    assert len(hist) == 10
    assert trim_history_tail_by_turns(hist, cap=9) is True
    assert len(hist) == 8


def test_redact_first_tool_output_in_text():
    raw = "**工具 `web_search`**（成功）\n- 参数：`{}`\n- 输出：\n```\nhello world\n```\ntail"
    out, ok = redact_first_tool_output_in_text(raw)
    assert ok
    assert TOOL_OUTPUT_REDACTED_PLACEHOLDER in out
    assert "hello world" not in out
    assert "tail" in out
    _, ok2 = redact_first_tool_output_in_text(out)
    assert not ok2


def test_compress_first_step_span():
    text = "[步骤 1/2] 做 A\n" + ("stream thinking " * 20) + "\n[步骤 2/2] 做 B\n" + "more"
    out, ok = compress_first_step_span_in_text(text)
    assert ok
    assert "stream thinking" not in out
    assert "[步骤 1/2]" in out
    assert "[步骤 2/2]" in out


def test_strip_thinking_l3():
    out, ok = strip_thinking_to_turn_summary("x" * 500)
    assert ok
    assert len(out) < 50


def test_apply_one_progressive_disk_step_on_history():
    hist = [
        {"role": "user", "content": "hi"},
        {
            "role": "thinking",
            "content": "**工具 `t`**（成功）\n- 参数：`[]`\n- 输出：\n````\nbody\n````\n",
        },
        {"role": "assistant", "content": "done"},
    ]
    ok, action = apply_one_progressive_disk_step(hist, session_key="sk")
    assert ok is True
    assert action is not None
    assert TOOL_OUTPUT_REDACTED_PLACEHOLDER in hist[1]["content"]


def test_run_session_history_maintenance_respects_progressive_off(tmp_path):
    install_test_config(
        tmp_path,
        {
            "paths": {"state_dir": str(tmp_path)},
            "memory": {"history_max_messages": 4, "history_progressive": False},
        },
    )
    hist = _turn("u1", "a1") + _turn("u2", "a2") + _turn("u3", "a3")
    run_session_history_maintenance("sk", hist, tail_cap=200, progressive_compression=False)
    assert len(hist) <= 4


def test_merge_agent_config_history_progressive_compression():
    from miniagent.core.config import get_default_agent_config, merge_agent_config

    base = get_default_agent_config()
    merged = merge_agent_config(base, {"history_progressive_compression": False})
    assert merged.history_progressive_compression is False


def test_redact_param_with_internal_backticks() -> None:
    raw = '**工具 `x`**（成功）\n- 参数：`{"a": "`quote`"}`\n- 输出：\n```\nOUT\n```\n'
    out, ok = redact_first_tool_output_in_text(raw)
    assert ok
    assert "OUT" not in out
    assert "`quote`" in out or "quote" in out


def test_compress_step_includes_plan_line_when_present() -> None:
    text = (
        "[执行计划]\n摘要\n\n步骤概要：\n"
        "2. Second step description here long enough\n\n"
        "[步骤 2/3] run this\n" + ("detail " * 30)
    )
    out, ok = compress_first_step_span_in_text(text)
    assert ok
    assert "Second step description" in out
    assert "detail " not in out


@pytest.mark.parametrize("fence", ("```", "````"))
def test_redact_varied_fence_width(fence: str):
    raw = f"**工具 `x`**（失败）\n- 参数：`{{}}`\n- 输出：\n{fence}\nBIG\n{fence}\n"
    out, ok = redact_first_tool_output_in_text(raw)
    assert ok
    assert "BIG" not in out
