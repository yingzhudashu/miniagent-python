"""Production runtime lifecycle graph assembly tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.bootstrap.lifecycle import LifecycleStartupError
from miniagent.assistant.bootstrap.runtime_services import build_runtime_lifecycle_manager
from miniagent.assistant.engine.cli_state import CliLoopState
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


class _FakeFeishuRuntime:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.running = False
        self.start_args: tuple[Any, ...] | None = None
        self.start_kwargs: dict[str, Any] | None = None

    def start(self, *args: Any, **kwargs: Any) -> None:
        self.events.append("start:feishu")
        self.start_args = args
        self.start_kwargs = kwargs
        self.running = True

    async def stop_async(self) -> None:
        self.events.append("stop:feishu")
        self.running = False

    def stop(self) -> None:
        self.running = False

    def is_running(self) -> bool:
        return self.running


def _make_ctx(feishu: Any) -> ApplicationContainer:
    return ApplicationContainer(
        registry=MagicMock(name="registry"),
        monitor=MagicMock(name="monitor"),
        skill_registry=MagicMock(name="skill_registry"),
        clawhub=MagicMock(name="clawhub"),
        engine=MagicMock(name="engine"),
        channel_router=MagicMock(name="channel_router"),
        message_queue=MagicMock(name="message_queue"),
        feishu=feishu,
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
    )


def _make_state(
    ctx: ApplicationContainer, *, feishu_enabled: bool = True
) -> CliLoopState:
    return {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": feishu_enabled,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }


@pytest.mark.asyncio
async def test_runtime_graph_preserves_production_order_and_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    feishu = _FakeFeishuRuntime(events)
    ctx = _make_ctx(feishu)
    state = _make_state(ctx)
    factory = MagicMock(name="handler_factory")
    status = MagicMock(name="user_status")
    ctx.create_feishu_handler_factory = factory
    toolboxes = [MagicMock(name="toolbox")]
    prompts = ["prompt"]
    captured: dict[str, tuple[Any, ...]] = {}

    async def run_task(name: str, stop_event: asyncio.Event) -> None:
        try:
            await stop_event.wait()
        finally:
            events.append(f"stop:{name}")

    def start_config(
        actual_ctx: ApplicationContainer,
        stop_event: asyncio.Event,
    ) -> asyncio.Task[Any]:
        events.append("start:config_watch")
        captured["config"] = (actual_ctx,)
        return asyncio.create_task(run_task("config_watch", stop_event))

    def start_scheduled(
        actual_ctx: ApplicationContainer,
        actual_state: CliLoopState,
        actual_toolboxes: list[Any],
        actual_prompts: list[Any],
        stop_event: asyncio.Event,
    ) -> asyncio.Task[Any]:
        events.append("start:scheduled_tasks")
        captured["scheduled"] = (
            actual_ctx,
            actual_state,
            actual_toolboxes,
            actual_prompts,
        )
        task = asyncio.create_task(run_task("scheduled_tasks", stop_event))
        return task

    def start_skills(
        registry: Any,
        skill_registry: Any,
        actual_state: dict[str, Any],
        stop_event: asyncio.Event,
    ) -> asyncio.Task[Any]:
        events.append("start:skills_watch")
        captured["skills"] = (registry, skill_registry, actual_state)
        task = asyncio.create_task(run_task("skills_watch", stop_event))
        return task

    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_config_watch",
        start_config,
    )
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_scheduled_tasks_ticker",
        start_scheduled,
    )
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_skills_watch",
        start_skills,
    )

    manager = build_runtime_lifecycle_manager(
        ctx,
        state,
        toolboxes,
        prompts,
        feishu_user_status=status,
    )
    assert manager.service_names == (
        "config_watch",
        "feishu",
        "scheduled_tasks",
        "skills_watch",
    )

    await manager.start()
    await asyncio.sleep(0)
    assert events == [
        "start:config_watch",
        "start:feishu",
        "start:scheduled_tasks",
        "start:skills_watch",
    ]
    assert feishu.start_args == (factory, state)
    assert feishu.start_kwargs == {"user_status": status}
    assert captured["scheduled"] == (ctx, state, toolboxes, prompts)
    assert captured["config"] == (ctx,)
    assert captured["skills"] == (ctx.registry, ctx.skill_registry, state)

    await manager.stop()
    assert events[-4:] == [
        "stop:skills_watch",
        "stop:scheduled_tasks",
        "stop:feishu",
        "stop:config_watch",
    ]


@pytest.mark.asyncio
async def test_runtime_graph_rolls_back_feishu_when_ticker_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    feishu = _FakeFeishuRuntime(events)
    ctx = _make_ctx(feishu)
    state = _make_state(ctx)
    ctx.create_feishu_handler_factory = MagicMock()
    skills_start = MagicMock()

    def fail_scheduled(*_args: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        events.append("start:scheduled_tasks")
        raise RuntimeError("ticker failed")

    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_config_watch",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_scheduled_tasks_ticker",
        fail_scheduled,
    )
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_skills_watch",
        skills_start,
    )
    manager = build_runtime_lifecycle_manager(ctx, state, [], [])

    with pytest.raises(LifecycleStartupError, match="scheduled_tasks"):
        await manager.start()

    assert events == ["start:feishu", "start:scheduled_tasks", "stop:feishu"]
    skills_start.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_graph_keeps_disabled_feishu_as_safe_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    feishu = _FakeFeishuRuntime(events)
    ctx = _make_ctx(feishu)
    state = _make_state(ctx, feishu_enabled=False)
    ctx.create_feishu_handler_factory = MagicMock()
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_config_watch",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_scheduled_tasks_ticker",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.runtime_services.start_skills_watch",
        lambda *_args: None,
    )
    manager = build_runtime_lifecycle_manager(ctx, state, [], [])

    await manager.start()
    await manager.stop()

    assert events == []
    assert manager.service_names[1] == "feishu"
