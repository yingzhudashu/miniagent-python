"""Direct behavior matrix for split command handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.assistant.engine.commands import (
    confirmation_commands,
    knowledge_commands,
    quality_commands,
    session_commands,
    test_commands,
)
from miniagent.assistant.engine.commands.knowledge_commands import _capture as capture_knowledge
from miniagent.assistant.engine.commands.knowledge_commands import _parse_search
from miniagent.assistant.engine.commands.markdown import (
    escape_markdown_cell,
)
from miniagent.assistant.engine.commands.queue_commands import (
    cmd_queue_set,
    cmd_queue_status,
    format_queue_abort_message,
    format_queue_command_usage,
)


@pytest.mark.asyncio
async def test_confirmation_handler_matrix(capsys) -> None:
    assert "未初始化" in await confirmation_commands.handle_confirmation(
        "/confirm", state={}, capture=True
    )
    channel = SimpleNamespace(has_pending=False, pending=None, respond=MagicMock())
    engine = SimpleNamespace(
        set_active_session_key=MagicMock(),
        get_confirmation_channel=lambda _key: channel,
    )
    state = {
        "runtime_ctx": SimpleNamespace(channel_router=SimpleNamespace()),
        "active_session_id": "s",
    }
    assert "无待确认" in await confirmation_commands.handle_confirmation(
        "/confirm", state=state, engine=engine, capture=True, confirmation_session_key="s"
    )
    channel.has_pending = True
    assert "已确认" in await confirmation_commands.handle_confirmation(
        "/confirm", state=state, engine=engine, capture=True, confirmation_session_key="s"
    )
    assert "已拒绝" in await confirmation_commands.handle_confirmation(
        "/reject", state=state, engine=engine, capture=True, confirmation_session_key="s"
    )
    long_adjustment = "x" * 70
    assert "…" in await confirmation_commands.handle_confirmation(
        f"/adjust {long_adjustment}",
        state=state,
        engine=engine,
        capture=True,
        confirmation_session_key="s",
    )

    from miniagent.agent.types.confirmation import ConfirmationStage

    channel.pending = SimpleNamespace(
        stage=ConfirmationStage.PLAN,
        full_content="plan" * 700,
        content="fallback",
    )
    usage = await confirmation_commands.handle_confirmation(
        "/adjust", state=state, engine=engine, capture=True, confirmation_session_key="s"
    )
    assert "当前完整计划" in usage and usage.endswith("…")
    await confirmation_commands.handle_confirmation(
        "/confirm", state=state, engine=engine, capture=False, confirmation_session_key="s"
    )
    assert "已确认" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_test_command_parsing_and_dispatch(monkeypatch, capsys) -> None:
    run = AsyncMock(return_value="run-output")
    monkeypatch.setattr("miniagent.assistant.engine.commands.test_commands._run_test", run)
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.test_commands._list_test_samples", lambda: "list"
    )
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.test_commands._get_test_status", lambda: "status"
    )
    runtime = SimpleNamespace(cli_transcript_append=MagicMock())
    state = {"runtime_ctx": runtime}

    assert (
        await test_commands.handle_test(
            "/test run real security case", state=state, capture=True, skill_prompts=["a", "b"]
        )
        == "run-output"
    )
    assert run.await_args.kwargs["mock"] is False
    assert run.await_args.kwargs["category"] == "security"
    assert run.await_args.kwargs["name_pattern"] == "case"
    assert run.await_args.kwargs["skill_prompts"] == "a\nb"
    assert await test_commands.handle_test("/test list", state=state, capture=True) == "list"
    assert await test_commands.handle_test("/test status", state=state, capture=True) == "status"
    assert test_commands._parse_run_arguments(["/test", "run", "core", "name"]) == (
        "mock",
        "core",
        "name",
    )
    await test_commands.handle_test("/test list", state=state, capture=False)
    assert "list" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_quality_handlers_and_persistence(monkeypatch, capsys) -> None:
    assert "需要会话" in await quality_commands.handle_review("/review", state={}, capture=True)
    state = {
        "runtime_ctx": SimpleNamespace(llm_gateway=None, cli_transcript_append=None),
        "session_manager": MagicMock(),
        "active_session_id": "s",
    }
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.quality_commands._get_last_qa",
        lambda *_args: (None, None),
    )
    assert "无历史" in await quality_commands.handle_review("/review", state=state, capture=True)
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.quality_commands._get_last_qa",
        lambda *_args: ("q", "a"),
    )
    review = AsyncMock(return_value="reviewed")
    monkeypatch.setattr("miniagent.assistant.engine.commands.quality_commands._run_review", review)
    assert (
        await quality_commands.handle_review("/review focus", state=state, capture=True)
        == "reviewed"
    )
    assert review.await_args.kwargs["extra_feedback"] == "focus"

    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.cmd_improve", lambda *_args, **_kwargs: ("message", True)
    )
    assert (
        await quality_commands.handle_improve("/improve --force", state=state, capture=True)
        == "message"
    )

    previous = {"content": "old", "metadata": {"improved": True, "improve_round": 2}}
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.cmd_improve",
        lambda *_args, **_kwargs: ({"content": "q"}, previous, ["better"]),
    )
    improve = AsyncMock(return_value="new")
    monkeypatch.setattr("miniagent.assistant.engine.commands.quality_commands._run_improve", improve)
    session = SimpleNamespace(conversation_history=[])
    manager = SimpleNamespace(
        get=lambda _sid: session,
        save_session_history_async=AsyncMock(),
    )
    state["session_manager"] = manager
    assert await quality_commands.handle_improve("/improve", state=state, capture=True) == "new"
    assert session.conversation_history[-1]["metadata"]["improve_round"] == 3
    manager.get = lambda _sid: None
    await quality_commands._persist_improved_answer(manager, "s", previous, "ignored")

    assert "需要会话" in await quality_commands.handle_improve("/improve", state={}, capture=True)
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.cmd_improve",
        lambda *_args, **_kwargs: ({"content": "q"}, previous, []),
    )
    improve.return_value = ""
    assert await quality_commands.handle_improve("/improve", state=state, capture=True) == ""
    await quality_commands.handle_review("/review", state=state, capture=False)
    await quality_commands.handle_improve("/improve", state=state, capture=False)
    assert "reviewed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_session_handler_full_dispatch_matrix(monkeypatch, capsys) -> None:
    assert "未初始化" in await session_commands.handle_session(
        "/session list", state={}, capture=True
    )
    manager = object()
    runtime = SimpleNamespace(channel_router=object())
    state = {
        "runtime_ctx": runtime,
        "session_manager": manager,
        "active_session_id": "old",
        "feishu_p2p_synced_senders": {"sender"},
    }
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.feishu_dot_commands_full_enabled", lambda: False
    )
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.feishu_markdown_commands_enabled", lambda: True
    )
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.format_session_command_usage", lambda: "usage"
    )
    assert "本地 MiniAgent" in await session_commands.handle_session(
        "/session switch next",
        state=state,
        capture=True,
        allow_session_mutations_when_capture=False,
    )

    leaves = {
        "cmd_session_list": MagicMock(side_effect=lambda *_a, **_k: print("listed")),
        "cmd_session_switch": AsyncMock(side_effect=lambda *_a, **_k: "next"),
        "cmd_session_create": AsyncMock(side_effect=lambda *_a, **_k: print("created")),
        "cmd_session_rename": MagicMock(side_effect=lambda *_a, **_k: print("renamed")),
            "cmd_session_delete": AsyncMock(side_effect=lambda *_a, **_k: print("deleted")),
    }
    with (
        patch("miniagent.assistant.engine.commands.session_management.cmd_session_list", leaves["cmd_session_list"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_session_switch", leaves["cmd_session_switch"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_session_create", leaves["cmd_session_create"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_session_rename", leaves["cmd_session_rename"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_session_delete", leaves["cmd_session_delete"]),
    ):
        assert (
            await session_commands.handle_session("/session list", state=state, capture=True)
            == "listed"
        )
        assert (
            await session_commands.handle_session("/session switch next", state=state, capture=True)
            == ""
        )
        assert state["active_session_id"] == "next"
        assert (
            await session_commands.handle_session(
                "/session create new title", state=state, capture=True
            )
            == "created"
        )
        assert (
            await session_commands.handle_session(
                "/session rename next New Title", state=state, capture=True
            )
            == "renamed"
        )
        assert (
            await session_commands.handle_session("/session delete next", state=state, capture=True)
            == "deleted"
        )
        assert (
            await session_commands.handle_session("/session unknown", state=state, capture=True)
            == "usage"
        )
        await session_commands.handle_session("/session list", state=state, capture=False)
    assert "listed" in capsys.readouterr().out

    with patch(
        "miniagent.assistant.engine.commands.session_management.cmd_session_switch",
        AsyncMock(side_effect=RuntimeError("switch failed")),
    ):
        assert "switch failed" in await session_commands.handle_session(
            "/session switch bad", state=state, capture=True
        )
    with patch(
        "miniagent.assistant.engine.commands.session_management.cmd_session_create",
        AsyncMock(side_effect=RuntimeError("create failed")),
    ):
        assert "create failed" in await session_commands.handle_session(
            "/session create bad", state=state, capture=True
        )
    assert "leaf failed" in session_commands._capture(
        lambda: (_ for _ in ()).throw(RuntimeError("leaf failed"))
    )


@pytest.mark.asyncio
async def test_knowledge_handler_full_dispatch_matrix(monkeypatch, capsys) -> None:
    assert "未初始化" in await knowledge_commands.handle_knowledge(
        "/knowledge", state={}, capture=True
    )
    registry = SimpleNamespace(list=lambda: [{"name": "docs"}])
    state = {"runtime_ctx": SimpleNamespace(knowledge_registry=registry)}
    monkeypatch.setattr(
        "miniagent.assistant.engine.commands.session_management.feishu_markdown_commands_enabled", lambda: True
    )
    monkeypatch.setattr("miniagent.assistant.engine.commands.session_management.format_kb_command_usage", lambda: "usage")
    leaves = {
        "list": MagicMock(side_effect=lambda *_a, **_k: print("listed")),
        "mount": MagicMock(return_value="mounted"),
        "unmount": MagicMock(return_value="unmounted"),
        "search": MagicMock(return_value="found"),
        "reload": MagicMock(return_value="reloaded"),
    }
    with (
        patch("miniagent.assistant.engine.commands.session_management.cmd_kb_list", leaves["list"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_kb_mount", leaves["mount"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_kb_unmount", leaves["unmount"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_kb_search", leaves["search"]),
        patch("miniagent.assistant.engine.commands.session_management.cmd_kb_reload", leaves["reload"]),
    ):
        assert (
            await knowledge_commands.handle_knowledge("/knowledge", state=state, capture=True)
            == "listed"
        )
        assert (
            await knowledge_commands.handle_knowledge(
                "/knowledge mount path alias", state=state, capture=True
            )
            == "mounted"
        )
        assert (
            await knowledge_commands.handle_knowledge(
                "/knowledge unmount docs", state=state, capture=True
            )
            == "unmounted"
        )
        assert (
            await knowledge_commands.handle_knowledge(
                "/knowledge search query docs", state=state, capture=True
            )
            == "found"
        )
        assert (
            await knowledge_commands.handle_knowledge(
                "/knowledge reload docs", state=state, capture=True
            )
            == "reloaded"
        )
        assert (
            await knowledge_commands.handle_knowledge("/knowledge bad", state=state, capture=True)
            == "usage"
        )
        await knowledge_commands.handle_knowledge("/knowledge list", state=state, capture=False)
    assert "listed" in capsys.readouterr().out


def test_knowledge_helpers_and_queue_leaf_commands(capsys) -> None:
    assert capture_knowledge(lambda: "result") == "result"
    assert "boom" in capture_knowledge(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    registry = SimpleNamespace(list=lambda: [{"name": "kb"}, {}])
    assert _parse_search(["/knowledge", "search", "query", "kb"], registry) == (
        "query",
        "kb",
    )
    assert _parse_search(["/knowledge", "search", "plain"], registry) == (
        "plain",
        None,
    )

    queue = SimpleNamespace(
        mode=SimpleNamespace(value="queue"),
        get_status=lambda: {
            "mode": "queue",
            "chats": {
                "a|b\n": {"busy": True, "pending": 2},
                "idle": {"busy": False, "pending": 0},
            },
        },
    )
    assert "当前模式" in format_queue_command_usage(queue)
    assert escape_markdown_cell("a|b\n") == "a\\|b"
    assert "无运行中" in format_queue_abort_message({})
    message = format_queue_abort_message(
        {
            "cancelled_running": True,
            "cancelled_pending": 2,
            "cancelled_preemptive_current": False,
            "cancelled_dispatch_wait": 1,
        }
    )
    assert "2 个" in message and "dispatch_wait" in message
    cmd_queue_status(queue, markdown=True)
    assert "| a\\|b |" in capsys.readouterr().out
    cmd_queue_status(queue)
    assert "处理中" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_queue_set_modes(capsys) -> None:
    queue = SimpleNamespace(mode=None)
    await cmd_queue_set(queue, "QUEUE")
    assert queue.mode.value == "queue"
    await cmd_queue_set(queue, "preemptive")
    assert queue.mode.value == "preemptive"
    await cmd_queue_set(queue, "bad")
    assert "未知模式" in capsys.readouterr().out
