"""依赖运行时资源的独立命令处理器回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.assistant.engine.commands.runtime_commands import (
    handle_abort,
    handle_background_task,
    handle_feishu,
    handle_query,
    handle_queue,
    handle_reload_skills,
    handle_stop,
)


class _Queue:
    CLI_CHAT_ID = "__cli__"

    def __init__(self) -> None:
        self.aborted: list[str] = []

    def abort_chat(self, chat_id: str) -> dict[str, object]:
        self.aborted.append(chat_id)
        return {"cancelled_running": True, "removed_pending": 2}


def _state(queue: _Queue | None = None) -> dict[str, object]:
    runtime = SimpleNamespace(
        message_queue=queue or _Queue(),
        skill_registry=object(),
    )
    return {"runtime_ctx": runtime, "session_manager": object()}


@pytest.mark.asyncio
async def test_abort_uses_channel_id_then_cli_default() -> None:
    queue = _Queue()
    state = _state(queue)
    with patch("miniagent.assistant.engine.commands.session_management.format_queue_abort_message", return_value="aborted"):
        assert (
            await handle_abort(
                "/abort",
                state=state,
                capture=True,
                message_queue_abort_chat_id="chat-1",
            )
            == "aborted"
        )
        await handle_abort("/abort", state=state, capture=True)
    assert queue.aborted == ["chat-1", "__cli__"]


@pytest.mark.asyncio
async def test_query_and_queue_cover_read_write_abort_and_usage() -> None:
    queue = _Queue()
    state = _state(queue)

    def print_status(*_args, **_kwargs) -> None:
        print("queue-status")

    async def set_mode(*_args, **_kwargs) -> None:
        print("queue-set")

    with (
        patch("miniagent.assistant.engine.commands.session_management.cmd_queue_status", side_effect=print_status),
        patch("miniagent.assistant.engine.commands.session_management.cmd_queue_set", side_effect=set_mode),
        patch(
            "miniagent.assistant.engine.commands.session_management.format_queue_abort_message",
            return_value="queue-abort",
        ),
        patch(
            "miniagent.assistant.engine.commands.session_management.format_queue_command_usage",
            return_value="queue-usage",
        ),
    ):
        assert await handle_query("/query", state=state, capture=True) == "queue-status"
        assert await handle_queue("/queue status", state=state, capture=True) == "queue-status"
        assert await handle_queue("/queue mode serial", state=state, capture=True) == "queue-set"
        assert await handle_queue("/queue abort", state=state, capture=True) == "queue-abort"
        assert await handle_queue("/queue", state=state, capture=True) == "queue-usage"


@pytest.mark.asyncio
async def test_queue_maps_set_failure() -> None:
    with patch(
        "miniagent.assistant.engine.commands.session_management.cmd_queue_set",
        AsyncMock(side_effect=ValueError("invalid mode")),
    ):
        output = await handle_queue("/queue set bad", state=_state(), capture=True)
    assert "invalid mode" in (output or "")


@pytest.mark.asyncio
async def test_reload_skills_reports_changes_and_failure() -> None:
    refresh = AsyncMock(
        return_value=SimpleNamespace(
            package_ids=["builtin"],
            loaded_skills=[1, 2],
            added_tools=["new"],
            removed_tools=[],
        )
    )
    with patch("miniagent.assistant.skills.refresh.refresh_skills", refresh):
        output = await handle_reload_skills(
            "/reload-skills", state=_state(), registry=object(), capture=True
        )
    assert "builtin" in (output or "")
    assert "技能数: 2" in (output or "")

    with patch(
        "miniagent.assistant.skills.refresh.refresh_skills",
        AsyncMock(side_effect=RuntimeError("refresh failed")),
    ):
        output = await handle_reload_skills(
            "/reload-skills", state=_state(), registry=object(), capture=True
        )
    assert "refresh failed" in (output or "")


@pytest.mark.asyncio
async def test_stop_enforces_channel_policy_and_shutdown_contract() -> None:
    state = _state()
    shutdown = AsyncMock()
    with (
        patch(
            "miniagent.assistant.engine.commands.session_management.feishu_dot_commands_full_enabled",
            return_value=False,
        ),
        patch("miniagent.assistant.engine.shutdown.shutdown_runtime", shutdown),
    ):
        assert "只能在 CLI" in (await handle_stop("/stop", state=state, capture=True) or "")
        assert await handle_stop("/stop", state=state, capture=False) == "__EXIT__"
    shutdown.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,text",
    [
        (handle_abort, "/abort"),
        (handle_query, "/query"),
        (handle_queue, "/queue"),
        (handle_reload_skills, "/reload-skills"),
        (handle_stop, "/stop"),
    ],
)
async def test_runtime_handlers_degrade_when_container_is_missing(handler, text) -> None:
    assert "未初始化" in (await handler(text, state={}, capture=True) or "")


@pytest.mark.asyncio
async def test_background_task_dispatch_and_missing_runtime() -> None:
    assert "未初始化" in await handle_background_task("/btw", state={}, capture=True)
    state = _state()
    state["runtime_ctx"].background_tasks = object()
    with (
        patch("miniagent.assistant.engine.btw_cmd.cmd_btw_start", AsyncMock(return_value="started")),
        patch("miniagent.assistant.engine.btw_cmd.cmd_btw_result", AsyncMock(return_value="result")),
        patch("miniagent.assistant.engine.btw_cmd.cmd_btw_cancel", AsyncMock(return_value="cancelled")),
        patch("miniagent.assistant.engine.btw_cmd.cmd_btw_clear", return_value="cleared"),
        patch("miniagent.assistant.engine.btw_cmd.cmd_btw_status", return_value="status"),
    ):
        assert (
            await handle_background_task("/btw start do work", state=state, capture=True)
            == "started"
        )
        assert (
            await handle_background_task("/btw result task", state=state, capture=True) == "result"
        )
        assert (
            await handle_background_task("/btw cancel task", state=state, capture=True)
            == "cancelled"
        )
        assert await handle_background_task("/btw clear", state=state, capture=True) == "cleared"
        assert (
            await handle_background_task("/btw status task", state=state, capture=True) == "status"
        )


@pytest.mark.asyncio
async def test_feishu_lifecycle_error_boundaries() -> None:
    assert "未初始化" in await handle_feishu("/feishu", state={}, capture=True)
    runtime = SimpleNamespace(lifecycle_manager=None)
    assert "生命周期服务未初始化" in await handle_feishu(
        "/feishu", state={"runtime_ctx": runtime}, capture=True
    )
    manager = SimpleNamespace(service=lambda _name: object())
    runtime.lifecycle_manager = manager
    assert "服务类型错误" in await handle_feishu(
        "/feishu", state={"runtime_ctx": runtime}, capture=True
    )

    class FakeFeishuService:
        def __init__(self) -> None:
            self.activate = AsyncMock()
            self.deactivate = AsyncMock()

    service = FakeFeishuService()
    runtime.lifecycle_manager = SimpleNamespace(service=lambda _name: service)
    runtime.feishu = SimpleNamespace(status=MagicMock(return_value="status"))
    with patch(
        "miniagent.assistant.engine.feishu_lifecycle.FeishuRuntimeLifecycleService",
        FakeFeishuService,
    ):
        assert (
            await handle_feishu("/feishu start", state={"runtime_ctx": runtime}, capture=True) == ""
        )
        assert (
            await handle_feishu("/feishu stop", state={"runtime_ctx": runtime}, capture=True) == ""
        )
        assert (
            await handle_feishu("/feishu", state={"runtime_ctx": runtime}, capture=True) == "status"
        )
        service.activate.side_effect = RuntimeError("activate failed")
        assert "activate failed" in await handle_feishu(
            "/feishu start", state={"runtime_ctx": runtime}, capture=True
        )
