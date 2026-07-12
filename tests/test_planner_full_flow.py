"""测试完整规划流程 — planner.py 的基本功能。

覆盖规划生成、错误处理、fallback 机制等。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.planner import _fallback_plan, generate_plan
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.tool import Toolbox
from tests.memory_helpers import make_knowledge_registry


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
        knowledge_registry=make_knowledge_registry(),
        client=mock_client,
    )

    # 应返回fallback计划
    assert isinstance(plan, StructuredPlan)
    assert plan.summary == "直接执行模式：跳过详细规划"
    assert len(plan.steps) == 1
    assert plan.steps[0].expected_input == "失败测试"
    assert plan.risk_level == "low"
    assert plan.suggested_config.max_turns == 5
    assert plan.fallback_plan.degrade_to_simple is False


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
        knowledge_registry=make_knowledge_registry(),
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

    plan = await generate_plan(
        "hello",
        [toolbox],
        knowledge_registry=make_knowledge_registry(),
        client=mock_client,
    )

    assert plan.summary == "t"
    assert captured["response_format"] == {"type": "json_object"}
    messages = captured["messages"]
    assert isinstance(messages, list)
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert any("json" in str(m.get("content", "")).lower() for m in user_messages)


@pytest.mark.asyncio
async def test_generate_plan_json_object_unsupported_downgrades_in_same_attempt():
    """API 不支持 json_object 时，同一次 attempt 内降级并重试。"""
    calls: list[dict] = []
    mock_client = AsyncMock()
    mock_response = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"summary":"downgraded","steps":[],"requiredToolboxes":[],'
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
        calls.append(dict(kw))
        if kw.get("response_format"):
            raise Exception("response_format json_object is not supported on this model")
        return mock_response

    mock_client.chat.completions.create = AsyncMock(side_effect=fake_create)
    toolbox = Toolbox(id="test", name="test", description="test", keywords=["test"])

    plan = await generate_plan(
        "hello",
        [toolbox],
        knowledge_registry=make_knowledge_registry(),
        client=mock_client,
    )

    assert plan.summary == "downgraded"
    assert len(calls) == 2
    assert calls[0].get("response_format") == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_fallback_plan_fields() -> None:
    """直接验证 fallback 计划的字段与策略。"""
    plan = _fallback_plan("用户原始问题")

    assert plan.summary == "直接执行模式：跳过详细规划"
    assert len(plan.steps) == 1
    assert plan.steps[0].description == "根据用户需求直接处理"
    assert plan.steps[0].expected_input == "用户原始问题"
    assert plan.steps[0].thinking_level == "low"
    assert plan.risk_level == "low"
    assert plan.suggested_config.max_turns == 5
    assert plan.suggested_config.risk_level == "low"
    assert plan.fallback_plan.degrade_to_simple is False
    assert plan.fallback_plan.degraded_max_turns == 5


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
