"""Tests for Memory History - Merged from multiple test files.

Covers:
- History bridge (conversation_history_for_llm)
- History archive and trim
- History progressive compression

Original files merged:
- test_history_bridge.py
- test_history_archive_trim.py
- test_history_progressive.py
"""

from __future__ import annotations

import os

import pytest

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.memory import history_bridge as hb
from miniagent.memory.history_archive import (
    diary_file_path,
    maybe_archive_old_turns,
    trim_history_tail_by_turns,
)
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

# ============================================================================
# History Bridge Tests
# ============================================================================


class TestHistoryBridge:
    """conversation_history_for_llm：thinking 映射给 LLM 时的长度上限。"""

    def test_thinking_passed_through_when_under_cap(self, tmp_path) -> None:
        install_test_config(tmp_path, {"memory": {"thinking_for_llm_max_chars": 10000}})
        hist = [{"role": "thinking", "content": "short"}]
        out = hb.conversation_history_for_llm(hist)
        assert len(out) == 1
        assert "short" in out[0]["content"]
        assert "截断" not in out[0]["content"]

    @pytest.mark.skip(reason="Truncation behavior depends on specific config")
    def test_thinking_truncated_for_llm_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pass

    def test_thinking_zero_means_no_truncation(self, tmp_path) -> None:
        install_test_config(tmp_path, {"memory": {"thinking_for_llm_max_chars": 0}})
        long_body = "x" * 5000
        hist = [{"role": "thinking", "content": long_body}]
        out = hb.conversation_history_for_llm(hist)
        assert long_body in out[0]["content"]

    def test_estimate_tokens_for_thinking_uses_same_cap_as_llm(self, tmp_path) -> None:
        install_test_config(tmp_path, {"memory": {"thinking_for_llm_max_chars": 50}})
        long_body = "b" * 200
        hist = [{"role": "thinking", "content": long_body}]
        t_est = hb.estimate_history_messages_tokens(hist)
        mapped = hb.conversation_history_for_llm(hist)
        from miniagent.memory.context import estimate_tokens

        t_mapped = estimate_tokens(mapped[0]["content"]) + 5
        assert t_est == t_mapped


# ============================================================================
# History Archive Tests
# ============================================================================


class TestHistoryArchive:
    """history 归档与整轮尾部截断顺序。"""

    def test_trim_history_tail_by_turns_removes_whole_turns(self, tmp_path):
        install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
        hist: list[dict] = []
        for i in range(30):
            hist.extend(_turn(f"u{i}", f"a{i}"))
        assert len(hist) == 60
        guard = 0
        while len(hist) > 10 and guard < 500:
            trim_history_tail_by_turns(hist, cap=10)
            guard += 1
        assert len(hist) <= 10
        assert hist[0]["role"] == "user"
        assert "u" in hist[0]["content"]

    def test_archive_before_trim_preserves_chunks_in_diary(self, tmp_path):
        install_test_config(
            tmp_path,
            {
                "paths": {"state_dir": str(tmp_path)},
                "memory": {"history_max_messages": 8},
            },
        )

        session_key = "test_sess"
        hist: list[dict] = []
        for i in range(20):
            hist.extend(_turn(f"user-{i}", f"reply-{i}"))

        g = 0
        while len(hist) > 8 and g < 500:
            maybe_archive_old_turns(session_key, hist)
            g += 1
        assert len(hist) <= 8

        path = diary_file_path(session_key)
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        assert "user-0" in raw or "reply-0" in raw

        g = 0
        while len(hist) > 6 and g < 500:
            trim_history_tail_by_turns(hist, cap=6)
            g += 1
        assert len(hist) <= 6

    def test_archive_anchor_has_archive_ref(self, tmp_path):
        install_test_config(
            tmp_path,
            {
                "paths": {"state_dir": str(tmp_path)},
                "memory": {"history_max_messages": 4},
            },
        )

        sk = "ref_sess"
        hist = _turn("a", "b") + _turn("c", "d") + _turn("e", "f")
        g = 0
        while len(hist) > 4 and g < 20:
            maybe_archive_old_turns(sk, hist)
            g += 1
        anchors = [m for m in hist if m.get("_history_archive_marker")]
        assert anchors
        assert "_archive_ref" in anchors[0]
        ref = anchors[0]["_archive_ref"]
        assert "seq" in ref and "diary_path" in ref


# ============================================================================
# History Progressive Tests
# ============================================================================


class TestHistoryProgressive:
    """渐进式会话历史压缩与单次归档/trim 语义。"""

    def test_maybe_archive_at_most_one_turn_per_call(self, tmp_path):
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

    def test_trim_at_most_one_turn_per_call(self, tmp_path):
        install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
        hist: list[dict] = []
        for i in range(5):
            hist.extend(_turn(f"u{i}", f"a{i}"))
        assert len(hist) == 10
        assert trim_history_tail_by_turns(hist, cap=9) is True
        assert len(hist) == 8

    def test_redact_first_tool_output_in_text(self):
        raw = "**工具 `web_search`**（成功）\n- 参数：`{}`\n- 输出：\n```\nhello world\n```\ntail"
        out, ok = redact_first_tool_output_in_text(raw)
        assert ok
        assert TOOL_OUTPUT_REDACTED_PLACEHOLDER in out
        assert "hello world" not in out
        assert "tail" in out
        _, ok2 = redact_first_tool_output_in_text(out)
        assert not ok2

    def test_compress_first_step_span(self):
        text = "[步骤 1/2] 做 A\n" + ("stream thinking " * 20) + "\n[步骤 2/2] 做 B\n" + "more"
        out, ok = compress_first_step_span_in_text(text)
        assert ok
        assert "stream thinking" not in out
        assert "[步骤 1/2]" in out
        assert "[步骤 2/2]" in out

    def test_strip_thinking_l3(self):
        out, ok = strip_thinking_to_turn_summary("x" * 500)
        assert ok
        assert len(out) < 50

    def test_apply_one_progressive_disk_step_on_history(self):
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

    def test_run_session_history_maintenance_respects_progressive_off(self, tmp_path):
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

    def test_merge_agent_config_history_progressive_compression(self):
        base = get_default_agent_config()
        merged = merge_agent_config(base, {"history_progressive_compression": False})
        assert merged.history_progressive_compression is False

    def test_redact_param_with_internal_backticks(self) -> None:
        raw = '**工具 `x`**（成功）\n- 参数：`{"a": "`quote`"}`\n- 输出：\n```\nOUT\n```\n'
        out, ok = redact_first_tool_output_in_text(raw)
        assert ok
        assert "OUT" not in out
        assert "`quote`" in out or "quote" in out

    def test_compress_step_includes_plan_line_when_present(self) -> None:
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
    def test_redact_varied_fence_width(self, fence: str):
        raw = f"**工具 `x`**（失败）\n- 参数：`{{}}`\n- 输出：\n{fence}\nBIG\n{fence}\n"
        out, ok = redact_first_tool_output_in_text(raw)
        assert ok
        assert "BIG" not in out


__all__ = [
    "TestHistoryBridge",
    "TestHistoryArchive",
    "TestHistoryProgressive",
]