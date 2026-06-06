"""规划阶段 on_thinking：合并为 ``[评估与计划]`` 单 header 流式推送。"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.core.agent import (
    PLANNING_STREAM_HEADER,
    _format_plan_message,
    _format_task_difficulty,
    run_agent,
)
from miniagent.core.task_classifier import TaskDifficulty
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.tool import Toolbox
from tests.config_helpers import install_test_config


def _make_mock_llm_client() -> MagicMock:
    """创建 mock LLM client，避免需要真实 API key。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()
    return mock_client


@contextlib.contextmanager
def _mock_all_llm_clients():
    """Mock 所有模块中的 get_shared_async_openai 引用。"""
    mock_client = _make_mock_llm_client()
    # Mock 源函数（延迟导入的模块会使用这个）
    # Mock 直接导入的模块
    patches = [
        patch("miniagent.core.openai_client.get_shared_async_openai", return_value=mock_client),
        patch("miniagent.core.task_classifier.get_shared_async_openai", return_value=mock_client),
        patch("miniagent.core.planner.get_shared_async_openai", return_value=mock_client),
        patch("miniagent.core.executor.get_shared_async_openai", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        yield mock_client
    finally:
        for p in patches:
            p.stop()


def test_format_task_difficulty() -> None:
    s = _format_task_difficulty(TaskDifficulty.MEDIUM)
    assert "任务难度" in s  # 新格式不含方括号标签
    assert "中等" in s


def test_format_plan_message_skipped_no_toolboxes() -> None:
    p = StructuredPlan(summary="直接执行模式", steps=[], required_toolboxes=[])
    t = _format_plan_message(p, from_llm_planner=False, no_toolboxes=True)
    assert "跳过结构化规划" in t  # 新格式不含方括号标签
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
async def test_plan_announce_before_execute_when_classifier_off(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
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

    with (
        patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.core.constants.EXECUTION_ANNOUNCE_DIFFICULTY", True),
        _mock_all_llm_clients(),
    ):
        with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
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
async def test_difficulty_announced_when_classifier_runs(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    captured: list[tuple[str, str]] = []

    async def ot(text: str, streaming: bool, header: str) -> None:
        captured.append((header, text))

    with (
        patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
        patch("miniagent.core.constants.EXECUTION_ANNOUNCE_DIFFICULTY", True),
        _mock_all_llm_clients(),
    ):
        with patch(
            "miniagent.core.agent.classify_task_difficulty",
            new_callable=AsyncMock,
        ) as clf:
            clf.return_value = TaskDifficulty.NORMAL
            with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
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
    # 输出应包含难度标签（display格式为 "**难度**"，full_record格式为 "任务难度：..."）
    assert "**难度**" in blob
    assert "s" in blob  # plan summary


@pytest.mark.asyncio
async def test_on_plan_reject_skips_plan_announce_and_execute(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
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

    with (
        patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.core.constants.EXECUTION_ANNOUNCE_DIFFICULTY", True),
        _mock_all_llm_clients(),
    ):
        with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
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
    # 当计划被拒绝时，不应发送计划相关内容
    assert not any("跳过结构化规划" in x or "摘要" in x for x in captured)
    ex.assert_not_called()


@pytest.mark.asyncio
async def test_skip_planning_announces_user_skip_not_simple(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    captured: list[str] = []

    async def ot(text: str, _stream: bool, _header: str) -> None:
        captured.append(text)

    with (
        patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
        patch("miniagent.core.constants.EXECUTION_ANNOUNCE_DIFFICULTY", True),
        _mock_all_llm_clients(),
    ):
        with patch(
            "miniagent.core.agent.classify_task_difficulty",
            new_callable=AsyncMock,
        ) as clf:
            clf.return_value = TaskDifficulty.NORMAL
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
    # display 格式为 "**难度**"，不含 "任务难度：" 标签
    assert "任务难度：" not in blob


@pytest.mark.asyncio
async def test_announce_disabled_skips_extra_on_thinking(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    captured: list[str] = []

    async def ot(text: str, streaming: bool, _header: str) -> None:
        captured.append(text[:80])

    with (
        patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
        patch("miniagent.core.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        _mock_all_llm_clients(),
    ):
        with patch("miniagent.core.agent.generate_plan", new_callable=AsyncMock) as gp:
            gp.return_value = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
            with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock):
                await run_agent(
                    "x",
                    registry=DefaultToolRegistry(),
                    toolboxes=[tb],
                    on_thinking=ot,
                )

    assert not any("执行计划" in x for x in captured)  # 新格式不含方括号标签
    assert not any("任务难度" in x for x in captured)
