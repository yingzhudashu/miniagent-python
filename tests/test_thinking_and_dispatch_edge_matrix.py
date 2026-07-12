"""思考流生命周期和命令分派显示降级矩阵。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine import command_dispatch
from miniagent.engine.thinking import ThinkingDisplay


@pytest.mark.asyncio
async def test_thinking_phase_finalize_legacy_callback_and_send_failure() -> None:
    sink: list[tuple[str, str]] = []

    def capture(text: str, kind: str = "chunk", **_kwargs: object) -> None:
        sink.append((text, kind))

    display = ThinkingDisplay()
    display.set_output_sink(capture)
    state = display._get_state("s")
    state.stream_header = "plan"
    state.stream_step = 1
    state.stream_done = False
    state.feishu_chat_id = "chat"
    state.feishu_thinking_message_id = "message"
    state.feishu_send = AsyncMock(side_effect=TypeError("legacy callback"))

    await display._reset_stream_phase(state, "execute", False)
    assert state.stream_step is None and state.stream_done

    state.feishu_send = AsyncMock(side_effect=RuntimeError("network"))
    await display._push_feishu_update(
        state,
        "thinking",
        "execute",
        streaming=True,
        merge_tools=False,
        is_last_step=False,
        session_key="s",
    )
    assert any("飞书发送失败" in text and kind == "label" for text, kind in sink)


@pytest.mark.asyncio
async def test_thinking_render_merge_last_step_and_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    sink: list[dict[str, object]] = []

    def capture(
        text: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
    ) -> None:
        sink.append(
            {
                "text": text,
                "kind": kind,
                "session_key": session_key,
                "ansi_markdown": ansi_markdown,
            }
        )

    display = ThinkingDisplay()
    display.set_output_sink(capture)
    monkeypatch.setattr("miniagent.engine.thinking._cli_thinking_rich_enabled", lambda: True)
    monkeypatch.setattr("miniagent.engine.thinking._cli_thinking_use_rich_render", lambda _body: True)
    monkeypatch.setattr(
        "miniagent.engine.markdown_cli.render_markdown_to_ansi",
        lambda _body, **_kwargs: "ANSI",
    )
    display._render_body("**body**", session_key="s")
    assert any(item["ansi_markdown"] == "ANSI" for item in sink)

    state = display._get_state("s")
    display._buffer_enabled = True
    display._show_merged_tools(state, "tool-a\ntool-b", session_key="s")
    assert state.buffer[-2:] == ["tool-a", "tool-b"]
    display._show_stream_event(state, "ignored", "last", is_last_step=True, session_key="s")

    state.stream_step = 1
    state.stream_done = False
    state.stream_header = "execute"
    state.feishu_chat_id = "chat"
    state.feishu_send = AsyncMock(side_effect=TypeError("old"))
    assert await display._finish_active_stream(state, session_key="s") == "execute"

    before = state.step_counter
    await display._show_non_stream_event(
        state, "最后一步开始", "last", is_last_step=True, session_key="s"
    )
    assert state.step_counter == before


@pytest.mark.asyncio
async def test_command_suggestion_capture_and_print(capsys) -> None:
    captured = await command_dispatch.dispatch_command("/statsx", state={}, capture=True)
    assert captured and "是否想输入" in captured
    printed = await command_dispatch.dispatch_command("/statsx", state={}, capture=False)
    assert printed is None and "是否想输入" in capsys.readouterr().out
    assert await command_dispatch.dispatch_command("plain text", state={}, capture=True) is None
    assert await command_dispatch.dispatch_command("   ", state={}, capture=True) is None


@pytest.mark.asyncio
async def test_review_and_improve_non_capture_outputs(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import miniagent.core.llm_json as llm_module

    review_responses = iter(
        [
            {"has_issues": True, "issues": [{"description": "issue"}]},
        ]
    )

    async def review(**_kwargs):
        return next(review_responses)

    monkeypatch.setattr(llm_module, "llm_json", review)
    assert await command_dispatch._run_review(
        "q", "a", capture=False, max_iterations=1
    ) is None
    assert "达到最大迭代次数" in capsys.readouterr().out

    async def improve(**_kwargs):
        return {"improved_answer": "better"}

    monkeypatch.setattr(llm_module, "llm_json", improve)
    assert await command_dispatch._run_improve("q", "a", ["clear"], capture=False) == "better"
    assert "better" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_self_test_non_capture_missing_registry(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    broken_sink = MagicMock(side_effect=RuntimeError("closed"))
    result = await command_dispatch._run_test(
        mock=False,
        registry=None,
        capture=False,
        term_write=broken_sink,
    )
    assert result == ""
    assert "需要 registry" in capsys.readouterr().out


def test_status_without_runtime() -> None:
    assert "未初始化" in command_dispatch._format_status({})
    assert command_dispatch._capture(lambda: print("ok")) == "ok"
