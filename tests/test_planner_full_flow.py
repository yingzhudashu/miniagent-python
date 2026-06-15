"""测试完整规划流程 — planner.py 的基本功能。

覆盖规划生成、错误处理、fallback 机制等。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.planner import generate_plan
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.tool import Toolbox


@pytest.mark.asyncio
async def test_generate_plan_fallback():
    """测试fallback降级机制。"""
    # 所有尝试都失败，应返回fallback计划
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=Exception("All retries failed")
    )

    toolbox = Toolbox(id="test", name="test", description="test", keywords=["test"])

    plan = await generate_plan(
        user_input="失败测试",
        toolboxes=[toolbox],
        client=mock_client,
    )

    # 应返回fallback计划
    assert isinstance(plan, StructuredPlan)
    assert plan.summary is not None  # fallback应包含基本summary


@pytest.mark.asyncio
async def test_generate_plan_basic_call():
    """测试基本规划调用。"""
    mock_client = AsyncMock()
    # 模拟任何响应，generate_plan会处理
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{}'))]
        )
    )

    toolbox = Toolbox(id="test", name="test", description="test", keywords=["test"])

    plan = await generate_plan(
        user_input="测试输入",
        toolboxes=[toolbox],
        client=mock_client,
    )

    # 应返回StructuredPlan对象
    assert isinstance(plan, StructuredPlan)
    assert isinstance(plan.steps, list)


@pytest.mark.asyncio
async def test_generate_plan_json_object_user_message_mentions_json():
    """Planner json_object requests must mention json in a user/input message."""
    captured: dict[str, object] = {}
    mock_client = AsyncMock()
    mock_response = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"summary":"t","steps":[],"requiredToolboxes":[],'
                        '"suggestedConfig":{},"estimatedTokens":{},'
                        '"contextStrategy":{},"requiresConfirmation":false,'
                        '"riskLevel":"low","estimatedCost":{},'
                        '"outputSpec":{},"fallbackPlan":{}}'
                    )
                )
            )
        ]
    )
    mock_response.usage = None

    async def fake_create(**kw):
        captured.update(kw)
        return mock_response

    mock_client.chat.completions.create = AsyncMock(side_effect=fake_create)
    toolbox = Toolbox(id="test", name="test", description="test", keywords=["test"])

    plan = await generate_plan("hello", [toolbox], client=mock_client)

    assert plan.summary == "t"
    assert captured["response_format"] == {"type": "json_object"}
    messages = captured["messages"]
    assert isinstance(messages, list)
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert any("json" in str(m.get("content", "")).lower() for m in user_messages)


def test_plan_step_dataclass():
    """测试规划步骤数据类。"""
    step = PlanStep(
        step_number=1,
        description="测试步骤",
        required_toolboxes=["test"],
        expected_input="输入",
        expected_output="输出",
        depends_on=None,
    )

    assert step.step_number == 1
    assert step.description == "测试步骤"
    assert step.required_toolboxes == ["test"]
    assert step.expected_input == "输入"
    assert step.expected_output == "输出"
    assert step.depends_on is None


def test_structured_plan_dataclass():
    """测试结构化计划数据类。"""
    plan = StructuredPlan(
        summary="测试规划",
        steps=[PlanStep(step_number=1, description="测试", required_toolboxes=["test"], expected_input="", expected_output="", depends_on=None)],
        required_toolboxes=["test"],
    )

    assert plan.summary == "测试规划"
    assert len(plan.steps) == 1
    assert plan.required_toolboxes == ["test"]


def test_toolbox_dataclass():
    """测试工具箱数据类。"""
    toolbox = Toolbox(
        id="web",
        name="网络搜索",
        description="网络搜索工具",
        keywords=["search", "web", "internet"],
    )

    assert toolbox.id == "web"
    assert toolbox.name == "网络搜索"
    assert toolbox.description == "网络搜索工具"
    assert len(toolbox.keywords) == 3
