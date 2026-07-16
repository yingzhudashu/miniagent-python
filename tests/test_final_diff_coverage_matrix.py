"""最终差异门禁所需的用户可见格式与生命周期边界。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import command_dispatch
from miniagent.assistant.engine.turn_service import _turn_label_sort_key, _TurnThinkingRecorder
from miniagent.assistant.feishu import card_rendering
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec

schedule_tools = importlib.import_module("miniagent.assistant.tools.schedule_tools")


def test_card_rendering_empty_cap_and_fence_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not card_rendering.is_important_content_for_immediate_patch("")
    assert card_rendering.normalize_lark_md("") == ""
    assert card_rendering.prepare_thinking_body_for_card("abcdef", max_len=3) == "abc…"
    assert card_rendering._chunk_cut_index("```python\ncode", 5) == len("```python\ncode")
    monkeypatch.setattr(card_rendering, "FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE", True)
    assert card_rendering.is_important_content_for_immediate_patch("# title")
    assert card_rendering.is_important_content_for_immediate_patch("|a|b|")


def test_updated_schedule_all_kinds_and_errors() -> None:
    existing = ScheduledTask(
        id="task",
        name="task",
        prompt="p",
        schedule=ScheduleSpec(kind="interval", interval_seconds=60, timezone="UTC"),
    )
    assert schedule_tools._updated_schedule(
        existing, {"schedule_kind": "interval", "interval_seconds": 5}, "UTC"
    ).interval_seconds == 5
    assert schedule_tools._updated_schedule(
        existing,
        {"schedule_kind": "once", "once_iso": "2035-01-01T00:00:00Z"},
        "UTC",
    ).kind == "once"
    assert schedule_tools._updated_schedule(
        existing, {"schedule_kind": "cron", "cron_expr": "0 1 * * *"}, "UTC"
    ).kind == "cron"
    with pytest.raises(ValueError, match="正整数"):
        schedule_tools._updated_schedule(
            existing, {"schedule_kind": "interval", "interval_seconds": 0}, "UTC"
        )
    with pytest.raises(ValueError, match="once_iso"):
        schedule_tools._updated_schedule(
            existing, {"schedule_kind": "once", "once_iso": ""}, "UTC"
        )


def test_set_enabled_repairs_missing_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    task = ScheduledTask(id="task", name="task", prompt="p", enabled=False, next_run_at=None)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.load_tasks", lambda: [task])
    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.store.compute_initial_next_run", lambda _task: 123.0
    )
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.repair_invalid_schedules", lambda _tasks: False)
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.save_tasks", MagicMock())
    result = schedule_tools._schedule_tool_set_enabled(
        {"task_id": "task", "enabled": True}
    )
    assert result.success and task.next_run_at == 123.0


@pytest.mark.asyncio
async def test_schedule_add_invalid_next_run_and_update_no_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.scheduled_tasks.store.compute_initial_next_run", lambda _task: None
    )
    add = schedule_tools._schedule_tool_add(
        {"action": "add_cron", "task_id": "x", "prompt": "p", "cron_expr": "0 1 * * *"}
    )
    assert not add.success and "cron" in add.content

    existing = ScheduledTask(id="x", name="x", prompt="old")
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.load_tasks", lambda: [existing])
    save = MagicMock()
    monkeypatch.setattr("miniagent.assistant.scheduled_tasks.store.save_tasks", save)
    update = schedule_tools._schedule_tool_update(
        {"action": "update", "task_id": "x", "prompt": "new", "interval_seconds": 10}
    )
    assert not update.success and "无法计算" in update.content
    save.assert_not_called()


@pytest.mark.asyncio
async def test_review_iterative_update_and_missing_improvement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.agent.llm_json as llm_module

    responses = iter(
        [
            {"has_issues": True, "issues": [{"description": "first"}], "improved_answer": "v1"},
            {"has_issues": True, "issues": [{"description": "second"}], "improved_answer": "v2"},
            {"has_issues": False, "issues": []},
            {"has_issues": True, "issues": [{"description": "first"}], "improved_answer": "v1"},
            {"has_issues": True, "issues": [{"description": "still"}]},
        ]
    )

    async def fake(**_kwargs):
        return next(responses)

    monkeypatch.setattr(llm_module, "llm_json", fake)
    assert "v2" in (await command_dispatch._run_review("q", "a", capture=True) or "")
    assert "v1" in (await command_dispatch._run_review("q", "a", capture=True) or "")


@pytest.mark.asyncio
async def test_self_test_real_builder_and_non_capture_return(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.testing.agent_adapter as adapter
    import miniagent.assistant.testing.test_runner as runner

    monkeypatch.setattr(adapter, "build_execute_agent_from_engine", AsyncMock(return_value="agent"))
    monkeypatch.setattr(
        runner,
        "run_self_test",
        AsyncMock(return_value=SimpleNamespace(passed=1, total=1, pass_rate=1.0, failed=0,
                                               skipped=0, duration_seconds=0.0, results=[])),
    )
    result = await command_dispatch._run_test(
        mock=False, registry=object(), capture=False, term_write=MagicMock()
    )
    assert result == ""


@pytest.mark.asyncio
async def test_turn_recorder_sort_reset_concat_and_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    display = SimpleNamespace(show=AsyncMock())
    recorder = _TurnThinkingRecorder(display=display, session_key="s")
    await recorder.on_thinking("old", True, "[执行]")
    await recorder.on_thinking("different", True, "[执行]")
    await recorder.on_thinking("new", True, "[执行]", reset=True)
    await recorder.on_tool_finish("read", "{}", "ok", True, thinking_header="[步骤 1/1]")
    blob = recorder.history_blob()
    assert "new" in blob and "read" in blob
    assert _turn_label_sort_key(("[步骤 2/3] x", ""))[0] == 0
    assert _turn_label_sort_key(("[评估与计划]", ""))[0] == 1
    assert _turn_label_sort_key(("[执行]", ""))[0] == 2
    assert _turn_label_sort_key(("[第 3 轮]", ""))[0] == 3
    assert _turn_label_sort_key(("other", ""))[0] == 4


def test_feishu_auto_bind_and_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.agent.types.confirmation import ConfirmationStage
    from miniagent.assistant.engine.feishu_handler import _FeishuHandlerRuntime

    runtime = object.__new__(_FeishuHandlerRuntime)
    router = SimpleNamespace(
        FEISHU_P2P_PREFIX="feishu_p2p:",
        is_bound=lambda _channel: False,
        bind=MagicMock(),
    )
    runtime.channel_router = router
    runtime.state = {"active_session_id": "active", "feishu_p2p_synced_senders": []}
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.cli_feishu_policy.should_allow_p2p_auto_bind", lambda _router: True
    )
    runtime.maybe_auto_bind("p2p", "sender")
    router.bind.assert_called_once()
    assert runtime.state["feishu_p2p_synced_senders"] == {"sender"}

    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.CLARIFICATION),
        respond=MagicMock(),
    )
    assert runtime._respond_clarification(channel, "answer")
    channel.respond.assert_called_once()
