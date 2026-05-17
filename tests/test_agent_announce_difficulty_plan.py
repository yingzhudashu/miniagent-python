"""规划阶段 on_thinking：合并为 ``[评估与计划]`` 单 header 流式推送。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from miniagent.core.agent import (
    PLANNING_STREAM_HEADER,
    _format_plan_message,
    _format_task_difficulty_message,
    run_agent,
)
from miniagent.core.task_classifier import TaskDifficulty
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.tool import Toolbox


def test_format_task_difficulty_message() -> None:
    s = _format_task_difficulty_message(TaskDifficulty.MEDIUM)
    assert "[任务难度]" in s
    assert "中等" in s


def test_format_plan_message_skipped_no_toolboxes() -> None:
    p = StructuredPlan(summary="直接执行模式", steps=[], required_toolboxes=[])
    t = _format_plan_message(p, from_llm_planner=False, no_toolboxes=True)
    assert "[执行计划]" in t
    assert "无可用工具箱" in t


def test_format_plan_message_skipped_user_skip() -> None:
    p = StructuredPlan(summary="直接执行模式", steps=[], required_toolboxes=[])
    t = _format_plan_message(p, from_llm_planner=False, user_skip_planning=True)
    assert "显式跳过规划" in t


def test_format_plan_message_skipped_simple() -> None:
    p = StructuredPlan(summary="直接执行模式", steps=[], required_toolboxes=[])
    t = _format_plan_message(p, from_llm_planner=False, simple_classified=True)
    assert "简单" in t


def test_format_plan_message_with_steps() -> None:
    p = StructuredPlan(
        summary="Do things",
        steps=[
            PlanStep(step_number=1, description="第一步"),
            PlanStep(step_number=2, description="第二步"),
        ],
        required_toolboxes=["fs"],
    )
    t = _format_plan_message(p, from_llm_planner=True)
    assert "Do things" in t
    assert "第一步" in t
    assert "fs" in t


def test_format_plan_lists_all_steps_without_ellipsis() -> None:
    steps = [
        PlanStep(
            step_number=i,
            description=f"描述{i}-" + "x" * 400,
            expected_input="in",
            expected_output="out",
        )
        for i in range(1, 31)
    ]
    plan = StructuredPlan(summary="摘要", steps=steps, required_toolboxes=["tb1", "tb2"])
    text = _format_plan_message(plan, from_llm_planner=True)
    assert "描述30-" in text
    assert "此处仅列前" not in text
    assert "预期输入：in" in text
    assert "涉及工具箱：tb1, tb2" in text


@pytest.mark.asyncio
async def test_plan_announce_before_execute_when_classifier_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "0")
    monkeypatch.setenv("MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN", "1")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])
    fake_plan = StructuredPlan(
        summary="plan summary unique",
        steps=[],
        required_toolboxes=[],
    )
    sequence: list[str] = []

    async def ot(text: str, streaming: bool, header: str) -> None:
        sequence.append(f"ot:{header}:{streaming}:{text[:40]}")

    async def fake_exec(*_a: object, **_k: object) -> str:
        sequence.append("execute_plan")
        return "ok"

    with patch("miniagent.core.planner.generate_plan", new_callable=AsyncMock) as gp:
        gp.return_value = fake_plan
        with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
            ex.side_effect = fake_exec
            await run_agent(
                "task",
                registry=DefaultToolRegistry(),
                toolboxes=[tb],
                on_thinking=ot,
            )

    assert sequence[0].startswith(f"ot:{PLANNING_STREAM_HEADER}:True:")
    joined = "".join(sequence)
    assert "plan summary unique" in joined
    assert sequence[-1] == "execute_plan"


@pytest.mark.asyncio
async def test_difficulty_announced_when_classifier_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "1")
    monkeypatch.setenv("MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN", "1")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    captured: list[tuple[str, str]] = []

    async def ot(text: str, streaming: bool, header: str) -> None:
        captured.append((header, text))

    with patch(
        "miniagent.core.task_classifier.classify_task_difficulty",
        new_callable=AsyncMock,
    ) as clf:
        clf.return_value = TaskDifficulty.NORMAL
        with patch("miniagent.core.planner.generate_plan", new_callable=AsyncMock) as gp:
            gp.return_value = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
            with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
                ex.return_value = "done"
                await run_agent(
                    "x",
                    registry=DefaultToolRegistry(),
                    toolboxes=[tb],
                    on_thinking=ot,
                )

    headers = [h for h, _ in captured]
    assert all(h == PLANNING_STREAM_HEADER for h in headers)
    blob = "\n".join(t for _, t in captured)
    assert "📋" in blob or "评估" in blob
    assert "**难度**" in blob
    assert "**计划**" in blob or "s" in blob


@pytest.mark.asyncio
async def test_on_plan_reject_skips_plan_announce_and_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "0")
    monkeypatch.setenv("MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN", "1")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    risky = StructuredPlan(
        summary="risk",
        steps=[],
        required_toolboxes=[],
        requires_confirmation=True,
    )

    captured: list[str] = []

    async def ot(text: str, _stream: bool, _header: str) -> None:
        captured.append(text)

    async def fake_on_plan(_plan: object) -> bool:
        return False

    with patch("miniagent.core.planner.generate_plan", new_callable=AsyncMock) as gp:
        gp.return_value = risky
        with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
            out = await run_agent(
                "x",
                registry=DefaultToolRegistry(),
                toolboxes=[tb],
                on_thinking=ot,
                on_plan=fake_on_plan,
            )

    assert "取消" in out or "取消" in str(out)
    assert not any("**计划**" in x or "[执行计划]" in x for x in captured)
    ex.assert_not_called()


@pytest.mark.asyncio
async def test_skip_planning_announces_user_skip_not_simple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "1")
    monkeypatch.setenv("MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN", "1")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    captured: list[str] = []

    async def ot(text: str, _stream: bool, _header: str) -> None:
        captured.append(text)

    with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as ex:
        ex.return_value = "ok"
        await run_agent(
            "task",
            registry=DefaultToolRegistry(),
            toolboxes=[tb],
            skip_planning=True,
            on_thinking=ot,
        )

    blob = "\n".join(captured)
    assert "显式跳过规划" in blob
    assert "[任务难度]" not in blob

@pytest.mark.asyncio
async def test_announce_disabled_skips_extra_on_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_TASK_CLASSIFIER", "0")
    monkeypatch.setenv("MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN", "0")
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    captured: list[str] = []

    async def ot(text: str, streaming: bool, _header: str) -> None:
        captured.append(text[:80])

    with patch("miniagent.core.planner.generate_plan", new_callable=AsyncMock) as gp:
        gp.return_value = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
        with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock):
            await run_agent(
                "x",
                registry=DefaultToolRegistry(),
                toolboxes=[tb],
                on_thinking=ot,
            )

    assert not any("[执行计划]" in x for x in captured)
    assert not any("[任务难度]" in x for x in captured)
