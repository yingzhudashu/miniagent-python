"""飞书思考：同轮工具合并为单条交互卡片（与 CLI merge_tools 对齐）。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from miniagent.feishu.types import FeishuConfig
from miniagent.types.agent import AgentRunResult
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime


@pytest.mark.asyncio
async def test_feishu_merge_tools_uses_append_not_second_send_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry

    calls: list[tuple[str, Any]] = []

    async def fake_push(*args: Any, **kwargs: Any) -> None:
        calls.append(("push", (args, kwargs)))
        st = args[4]
        st.feishu_stream_accumulated = args[2]
        st.feishu_thinking_message_id = "m1"

    async def fake_append(*args: Any, **kwargs: Any) -> None:
        calls.append(("append", (args, kwargs)))
        st = args[4]
        line = (args[2] or "").strip()
        acc = getattr(st, "feishu_stream_accumulated", "") or ""
        if not getattr(st, "feishu_tool_section_started", False):
            st.feishu_tool_section_started = True
            st.feishu_stream_accumulated = (
                acc + "\n\n---\n\n**工具**\n\n- " + line.replace("\n", " ")
            )
        else:
            st.feishu_stream_accumulated = acc + "\n- " + line.replace("\n", " ")

    async def fake_finalize(*args: Any, **kwargs: Any) -> None:
        calls.append(("finalize", (args, kwargs)))

    async def fake_send_thinking(*args: Any, **kwargs: Any) -> None:
        calls.append(("send_thinking", (args, kwargs)))

    monkeypatch.setattr("miniagent.feishu.poll_server.push_feishu_thinking_stream", fake_push)
    monkeypatch.setattr(
        "miniagent.feishu.poll_server.append_feishu_thinking_same_card", fake_append
    )
    monkeypatch.setattr(
        "miniagent.feishu.poll_server.finalize_feishu_thinking_stream", fake_finalize
    )
    monkeypatch.setattr("miniagent.feishu.poll_server._send_thinking", fake_send_thinking)

    async def fake_run_agent(*args: Any, **kwargs: Any) -> AgentRunResult:
        ot = kwargs.get("on_thinking")
        lab = "[第 1 轮]"
        await ot(lab, True, lab)
        await ot(lab + "正文", True, lab)
        await ot("🔧 a — 1", False, lab)
        await ot("🔧 b — 2", False, lab)
        return AgentRunResult(reply="ok")

    monkeypatch.setattr("miniagent.engine.engine.run_agent", fake_run_agent)

    ctx = type("Ctx", (), {})()
    ctx.conversation_history = []

    class SM:
        def get_or_create(self, sk: Any, opts: Any) -> Any:
            return ctx

        def get_session_files_path(self, sk: str) -> None:
            return None

        def save_session_history(self, sk: str) -> None:
            pass

    from miniagent.infrastructure.channel_router import ChannelRouter

    router = ChannelRouter()
    engine = UnifiedEngine()
    engine.thinking.set_output_sink(lambda *_a, **_k: None)

    cfg = FeishuConfig(app_id="x", app_secret="y")

    await engine.run_agent_with_thinking(
        "hi",
        "feishu:oc_testchat",
        [],
        None,
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        client=MagicMock(),
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        session_manager=SM(),
        is_feishu=True,
        feishu_config=cfg,
        channel_router=router,
        feishu_receive_chat_id="oc_testchat",
    )

    kinds = [c[0] for c in calls]
    assert kinds.count("append") == 2
    assert kinds.count("send_thinking") == 0
    assert "finalize" in kinds


@pytest.mark.asyncio
async def test_thinking_show_passes_merge_tools_to_feishu(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    td.set_output_sink(lambda *_a, **_k: None)

    received: list[dict[str, Any]] = []

    async def feishu_send(
        chat_id: str,
        text: str,
        template: str,
        **kw: Any,
    ) -> None:
        received.append({"chat_id": chat_id, "text": text, "template": template, **kw})

    td.enable_feishu("sk1", "oc_x", feishu_send)
    lab = "[第 1 轮]"
    await td.show(lab, session_key="sk1", streaming=True, header=lab)
    await td.show(lab + "hi", session_key="sk1", streaming=True, header=lab)
    await td.show("🔧 t — i", session_key="sk1", streaming=False, header=lab)

    assert received[-1]["merge_tools"] is True
    assert received[-1]["text"] == "🔧 t — i"


@pytest.mark.asyncio
async def test_append_feishu_thinking_same_card_updates_accumulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.feishu.thinking_delivery as ps
    from miniagent.engine.thinking import ThinkingDisplay
    from miniagent.feishu.poll_server import append_feishu_thinking_same_card

    td = ThinkingDisplay()
    st = td.thinking_state("sk")
    st.feishu_thinking_message_id = None
    st.feishu_stream_accumulated = "[第 1 轮]hello"
    st.feishu_tool_section_started = False
    st.feishu_pending_tool_lines = []

    monkeypatch.setattr(ps, "_create_interactive_thinking_message", lambda *_a, **_k: "newmid")

    cfg = FeishuConfig(app_id="a", app_secret="b")
    await append_feishu_thinking_same_card(cfg, "oc_x", "🔧 z — q", "gray", st)

    # 新行为：无卡片时缓冲工具行，不创建新卡
    assert "**工具**" in st.feishu_stream_accumulated
    assert "🔧 z — q" in st.feishu_stream_accumulated
    assert st.feishu_thinking_message_id is None  # 不创建独立卡片
    assert len(st.feishu_pending_tool_lines) == 1  # 工具行已缓冲


@pytest.mark.asyncio
async def test_append_feishu_with_message_id_patches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.feishu.thinking_delivery as ps
    from miniagent.engine.thinking import ThinkingDisplay
    from miniagent.feishu.poll_server import append_feishu_thinking_same_card

    td = ThinkingDisplay()
    st = td.thinking_state("sk2")
    st.feishu_thinking_message_id = "mid-1"
    st.feishu_stream_accumulated = "stream body"
    st.feishu_tool_section_started = False
    patched: list[str] = []

    async def _patch_async(_c: Any, mid: str, card_json: str, timeout: float = 10.0) -> bool:
        patched.append(card_json)
        return True

    monkeypatch.setattr(ps, "_patch_interactive_thinking_message_async", _patch_async)

    cfg = FeishuConfig(app_id="a", app_secret="b")
    await append_feishu_thinking_same_card(cfg, "oc_x", "🔧 x — y", "gray", st)

    assert len(patched) == 1
    assert "🔧 x — y" in st.feishu_stream_accumulated
    assert st.feishu_thinking_message_id == "mid-1"


def test_thinking_card_json_cache_reuses_same_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.feishu.thinking_delivery as ps
    from miniagent.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    st = td.thinking_state("sk-cache")
    calls = {"n": 0}

    def fake_prepare(raw: str) -> str:
        calls["n"] += 1
        return f"clean:{raw}"

    monkeypatch.setattr(ps, "_prepare_thinking_markdown", fake_prepare)

    first = ps._thinking_card_json_cached(st, "body", "gray", "sk-cache")
    second = ps._thinking_card_json_cached(st, "body", "gray", "sk-cache")
    third = ps._thinking_card_json_cached(st, "body2", "gray", "sk-cache")

    assert first == second
    assert first != third
    assert calls["n"] == 2
