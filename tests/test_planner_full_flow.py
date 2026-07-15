"""测试完整规划流程 — planner.py 的基本功能。

覆盖规划生成、错误处理、fallback 机制等。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.planner import _fallback_plan, generate_plan
from miniagent.agent.types.config import AgentConfig, ModelConfig, SessionBindingConfig, WireAPI
from miniagent.agent.types.planning import PlanStep, StructuredPlan
from miniagent.agent.types.tool import Toolbox
from tests.memory_helpers import make_knowledge_registry

_VALID_PLAN_CONTENT = (
    '{"summary":"configured","steps":[],"requiredToolboxes":[],'
    '"suggestedConfig":{},"estimatedTokens":{},'
    '"contextStrategy":{},"requiresConfirmation":false,'
    '"riskLevel":"low","estimatedCost":{},'
    '"outputSpec":{},"fallbackPlan":{}}'
)


def _planner_response(content: str = _VALID_PLAN_CONTENT) -> MagicMock:
    response = MagicMock(choices=[MagicMock(message=MagicMock(content=content))])
    response.usage = None
    return response


def _responses_planner_response(
    content: str | None = _VALID_PLAN_CONTENT,
    *,
    status: str = "completed",
    output_types: tuple[str, ...] = ("message",),
    incomplete_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        output_text=content,
        output=[SimpleNamespace(type=item_type) for item_type in output_types],
        status=status,
        incomplete_details=(
            SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None
        ),
        usage=None,
        model="response-model",
    )


def _responses_stream(content: str | None) -> object:
    async def events():
        if content:
            yield SimpleNamespace(type="response.output_text.delta", delta=content)
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        )

    return events()


@contextmanager
def _model_protocol(
    wire_api: WireAPI, *, max_tokens: int = 4096
) -> Iterator[None]:
    config = ModelConfig(
        model="response-model",
        wire_api=wire_api,
        max_tokens=max_tokens,
    )
    with (
        patch("miniagent.agent.config.get_default_model_config", return_value=config),
        patch("miniagent.agent.llm_params.get_default_model_config", return_value=config),
        patch(
            "miniagent.agent.planner.get_config",
            side_effect=lambda path, default=None: wire_api
            if path == "model.wire_api"
            else default,
        ),
    ):
        yield


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


@pytest.mark.asyncio
async def test_responses_planner_recovers_reasoning_only_without_sampling() -> None:
    events: list[dict[str, object]] = []
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            _responses_planner_response(None, output_types=("reasoning",)),
            _responses_planner_response(),
        ]
    )

    with (
        _model_protocol("responses"),
        patch(
            "miniagent.agent.observability.emit_trace",
            side_effect=lambda event: events.append(event),
        ),
    ):
        plan = await generate_plan(
            "recover",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
            planner_model_overrides={"thinking_level": "heavy"},
        )

    assert plan.summary == "configured"
    calls = client.responses.create.await_args_list
    assert all(call.kwargs["stream"] is True for call in calls)
    assert calls[0].kwargs["reasoning"] == {"effort": "high"}
    assert calls[0].kwargs["temperature"] == 0.3
    assert calls[0].kwargs["top_p"] == 1.0
    assert calls[1].kwargs["reasoning"] == {"effort": "high"}
    assert "temperature" not in calls[1].kwargs
    assert "top_p" not in calls[1].kwargs
    first_response = next(
        event
        for event in events
        if event.get("type") == "llm.response" and event.get("attempt") == 1
    )
    assert first_response["failure_category"] == "reasoning_only"
    assert first_response["output_item_types"] == ["reasoning"]
    assert isinstance(first_response["duration_ms"], int)
    assert first_response["duration_ms"] >= 0
    first_request = next(
        event
        for event in events
        if event.get("type") == "llm.request" and event.get("attempt") == 1
    )
    assert first_request["message_count"] == 2
    assert first_request["tool_count"] == 0


@pytest.mark.asyncio
async def test_responses_planner_only_expands_budget_for_incomplete_output() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            _responses_planner_response(
                None,
                status="incomplete",
                output_types=("reasoning",),
                incomplete_reason="max_output_tokens",
            ),
            _responses_planner_response(),
        ]
    )

    with _model_protocol("responses", max_tokens=4096):
        plan = await generate_plan(
            "expand budget",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert plan.summary == "configured"
    calls = client.responses.create.await_args_list
    assert calls[0].kwargs["max_output_tokens"] == 2048
    assert calls[1].kwargs["max_output_tokens"] == 4096
    assert "temperature" not in calls[1].kwargs
    assert "top_p" not in calls[1].kwargs


@pytest.mark.asyncio
async def test_responses_planner_uses_medium_for_final_empty_recovery() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            _responses_planner_response(None, output_types=("reasoning",)),
            _responses_planner_response(None, output_types=("reasoning",)),
            _responses_stream(_VALID_PLAN_CONTENT),
        ]
    )

    with _model_protocol("responses"):
        plan = await generate_plan(
            "final recovery",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
            planner_model_overrides={"thinking_level": "heavy"},
        )

    assert plan.summary == "configured"
    calls = client.responses.create.await_args_list
    assert all(call.kwargs["stream"] is True for call in calls)
    assert [call.kwargs["reasoning"] for call in calls] == [
        {"effort": "high"},
        {"effort": "high"},
        {"effort": "medium"},
    ]
    assert "temperature" not in calls[2].kwargs
    assert "top_p" not in calls[2].kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_content", ["not-json", "{}"])
async def test_responses_planner_uses_medium_after_repeated_invalid_plan(
    invalid_content: str,
) -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            _responses_planner_response(invalid_content),
            _responses_planner_response(invalid_content),
            _responses_stream(_VALID_PLAN_CONTENT),
        ]
    )

    with _model_protocol("responses"):
        plan = await generate_plan(
            "repair invalid plan",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
            planner_model_overrides={"thinking_level": "heavy"},
        )

    assert plan.summary == "configured"
    assert client.responses.create.await_args_list[2].kwargs["reasoning"] == {
        "effort": "medium"
    }


@pytest.mark.asyncio
async def test_responses_planner_falls_back_after_three_reasoning_only_responses(
) -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[
            _responses_planner_response(None, output_types=("reasoning",)),
            _responses_planner_response(None, output_types=("reasoning",)),
            _responses_stream(None),
        ]
    )
    secret_marker = "must-not-appear-in-planner-log"

    with (
        _model_protocol("responses"),
        patch("miniagent.agent.planner._logger.warning") as warning,
    ):
        plan = await generate_plan(
            secret_marker,
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert plan.summary == "直接执行模式：跳过详细规划"
    assert client.responses.create.await_count == 3
    logged = " ".join(str(call.args) for call in warning.call_args_list)
    assert "reasoning_only,reasoning_only,completed_without_text" in logged
    assert secret_marker not in logged


@pytest.mark.asyncio
async def test_responses_planner_recovers_generic_400_without_sampling() -> None:
    class GenericBadRequest(Exception):
        status_code = 400

    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[GenericBadRequest("invalid_request_error"), _responses_planner_response()]
    )

    with _model_protocol("responses"):
        plan = await generate_plan(
            "retry generic request failure",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert plan.summary == "configured"
    second = client.responses.create.await_args_list[1].kwargs
    assert "temperature" not in second
    assert "top_p" not in second


@pytest.mark.asyncio
async def test_planner_does_not_retry_deterministic_authentication_error() -> None:
    class AuthenticationFailure(Exception):
        status_code = 401

    client = MagicMock()
    client.responses.create = AsyncMock(side_effect=AuthenticationFailure("unauthorized"))

    with _model_protocol("responses"):
        plan = await generate_plan(
            "authentication failure",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert plan.summary == "直接执行模式：跳过详细规划"
    assert client.responses.create.await_count == 1


@pytest.mark.asyncio
async def test_chat_planner_retries_without_changing_sampling_parameters() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=[_planner_response(""), _planner_response()]
    )

    with _model_protocol("chat_completions"):
        plan = await generate_plan(
            "chat retry",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=client,
        )

    assert plan.summary == "configured"
    second = client.chat.completions.create.await_args_list[1].kwargs
    assert second["temperature"] == 0.3
    assert second["top_p"] == 1.0


@pytest.mark.asyncio
async def test_generate_plan_uses_grouped_session_config_and_history() -> None:
    """规划器从分组配置读取会话键与历史上下文。"""
    events: list[dict[str, object]] = []
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_planner_response())
    config = AgentConfig(
        session_config=SessionBindingConfig(
            session_key="nested-session",
            conversation_history=[
                {"role": "assistant", "content": "已完成 pytest 回归验证"}
            ],
        )
    )

    with patch(
        "miniagent.agent.observability.emit_trace",
        side_effect=lambda event: events.append(event),
    ):
        plan = await generate_plan(
            "继续完成任务",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=mock_client,
            agent_config=config,
        )

    assert plan.summary == "configured"
    planning_events = [event for event in events if event.get("phase") == "plan"]
    assert [event["session_key"] for event in planning_events] == [
        "nested-session",
        "nested-session",
    ]
    messages = mock_client.chat.completions.create.await_args.kwargs["messages"]
    assert any("已完成 pytest 回归验证" in message["content"] for message in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, AgentConfig()])
async def test_generate_plan_defaults_missing_or_empty_session_key(
    config: AgentConfig | None,
) -> None:
    """缺少有效会话键时，规划追踪稳定回退到 default。"""
    events: list[dict[str, object]] = []
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_planner_response())

    with patch(
        "miniagent.agent.observability.emit_trace",
        side_effect=lambda event: events.append(event),
    ):
        await generate_plan(
            "普通任务",
            [],
            knowledge_registry=make_knowledge_registry(),
            client=mock_client,
            agent_config=config,
        )

    planning_events = [event for event in events if event.get("phase") == "plan"]
    assert [event["session_key"] for event in planning_events] == ["default", "default"]


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
