"""run_agent + 任务分类：简单路径不调用 generate_plan。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.agent import run_agent
from miniagent.agent.requirement_clarifier import RequirementClarifier
from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.agent.types.tool import Toolbox
from tests.config_helpers import install_test_config
from tests.llm_helpers import MockGateway
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime


@pytest.mark.asyncio
async def test_simple_difficulty_skips_planner(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
    ):
        with patch(
            "miniagent.agent.agent.classify_task_difficulty",
            new_callable=AsyncMock,
        ) as clf:
            from miniagent.agent.task_classifier import TaskDifficulty

            clf.return_value = TaskDifficulty.SIMPLE
            with patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp:
                with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex:
                    ex.return_value = "ok"
                    out = await run_agent(
                        "hello",
                        registry=DefaultToolRegistry(),
                        memory=make_memory_runtime(),
                        knowledge_registry=make_knowledge_registry(),
                        client=MagicMock(),
                        toolboxes=[tb],
                    )
    assert out.reply == "ok"
    gp.assert_not_called()
    ex.assert_called_once()


@pytest.mark.asyncio
async def test_top_level_session_key_propagates_to_all_agent_stages(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    toolbox = Toolbox(id="fs", name="fs", description="files", keywords=[])
    from miniagent.agent.task_classifier import TaskDifficulty
    from miniagent.agent.types.planning import StructuredPlan

    plan = StructuredPlan(summary="x", steps=[], required_toolboxes=[])
    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
        patch(
            "miniagent.agent.agent.classify_task_difficulty",
            new_callable=AsyncMock,
            return_value=TaskDifficulty.NORMAL,
        ) as classify,
        patch(
            "miniagent.agent.agent.generate_plan",
            new_callable=AsyncMock,
            return_value=plan,
        ) as planner,
        patch(
            "miniagent.agent.agent.execute_plan",
            new_callable=AsyncMock,
            return_value="done",
        ) as execute,
    ):
        await run_agent(
            "task",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            toolboxes=[toolbox],
            session_key="top-level-session",
        )

    classify_config = classify.await_args.kwargs["agent_config"]
    planner_config = planner.await_args.kwargs["agent_config"]
    execute_config = execute.await_args.args[4]
    assert classify_config.session_config.session_key == "top-level-session"
    assert planner_config.session_config.session_key == "top-level-session"
    assert execute_config.session_config.session_key == "top-level-session"


@pytest.mark.asyncio
async def test_run_agent_owns_activity_lifecycle_once(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    from miniagent.assistant.memory.activity_log import ActivityLogger

    activity_log = ActivityLogger(base_dir=str(tmp_path / "activity"))
    with patch(
        "miniagent.agent.agent.execute_plan",
        new_callable=AsyncMock,
        return_value="done",
    ) as execute:
        result = await run_agent(
            "hello",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(activity_log=activity_log),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            toolboxes=[],
            session_key="activity-session",
        )

    content = next((tmp_path / "activity").glob("*.md")).read_text(encoding="utf-8")
    assert result.reply == "done"
    assert content.count("### 用户输入") == 1
    assert content.count("### 最终回复") == 1
    assert execute.await_args.kwargs["manage_activity_lifecycle"] is False


@pytest.mark.asyncio
async def test_classifier_off_always_plans(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    from miniagent.agent.types.planning import StructuredPlan

    fake_plan = StructuredPlan(summary="x", steps=[], required_toolboxes=[])

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
    ):
        with patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp:
            gp.return_value = fake_plan
            with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex:
                ex.return_value = "done"
                await run_agent(
                    "task",
                    registry=DefaultToolRegistry(),
                    memory=make_memory_runtime(),
                    knowledge_registry=make_knowledge_registry(),
                    client=MagicMock(),
                    toolboxes=[tb],
                )
    gp.assert_called_once()
    ex.assert_called_once()


@pytest.mark.asyncio
async def test_run_agent_real_planner_accepts_grouped_session_config(tmp_path) -> None:
    """生产规划链路应直接消费分组 AgentConfig，不依赖已删除的平铺字段。"""
    install_test_config(tmp_path, {"features": {"reflection": False}})
    toolbox = Toolbox(id="fs", name="fs", description="files", keywords=[])
    raw_client = MagicMock()
    response = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"summary":"real planner","steps":[],"requiredToolboxes":[],'
                        '"suggestedConfig":{},"estimatedTokens":{},'
                        '"contextStrategy":{},"requiresConfirmation":false,'
                        '"riskLevel":"low","estimatedCost":{},'
                        '"outputSpec":{},"fallbackPlan":{}}'
                    )
                )
            )
        ]
    )
    response.usage = None
    raw_client.chat.completions.create = AsyncMock(return_value=response)
    mock_client = MockGateway(raw_client)

    with patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False):
        with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as execute:
            execute.return_value = "done"
            result = await run_agent(
                "task",
                registry=DefaultToolRegistry(),
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=mock_client,
                toolboxes=[toolbox],
                agent_config={
                    "session_config": {
                        "session_key": "integration-session",
                        "conversation_history": [
                            {"role": "assistant", "content": "已完成集成准备"}
                        ],
                    }
                },
            )

    assert result.reply == "done"
    raw_client.chat.completions.create.assert_awaited_once()
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_agent_real_planner_supports_responses_wire_api(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {"features": {"reflection": False}},
    )
    toolbox = Toolbox(id="fs", name="fs", description="files", keywords=[])
    content = (
        '{"summary":"responses planner","steps":[],"requiredToolboxes":[],'
        '"suggestedConfig":{},"estimatedTokens":{},'
        '"contextStrategy":{},"requiresConfirmation":false,'
        '"riskLevel":"low","estimatedCost":{},'
        '"outputSpec":{},"fallbackPlan":{}}'
    )
    async def response_events():
        yield SimpleNamespace(
            type="response.output_text.delta",
            output_index=0,
            content_index=0,
            delta=content,
        )
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                status="completed",
                output=[SimpleNamespace(type="message")],
                usage=None,
                model="response-model",
            ),
        )

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=response_events())

    with patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False):
        with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as execute:
            execute.return_value = "done"
            result = await run_agent(
                "task",
                registry=DefaultToolRegistry(),
                memory=make_memory_runtime(),
                knowledge_registry=make_knowledge_registry(),
                client=MockGateway(client, responses=True),
                toolboxes=[toolbox],
            )

    assert result.reply == "done"
    client.responses.create.assert_awaited_once()
    assert client.responses.create.await_args.kwargs["stream"] is True
    assert "response_format" not in client.responses.create.await_args.kwargs
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_agent_responses_control_chain_streams_all_json_stages(
    tmp_path,
) -> None:
    install_test_config(
        tmp_path,
        {
            "features": {"reflection": True, "requirement_clarify": True},
        },
    )
    toolbox = Toolbox(id="fs", name="fs", description="files", keywords=[])
    plan_content = (
        '{"summary":"chain planner","steps":[],"requiredToolboxes":[],'
        '"suggestedConfig":{},"estimatedTokens":{},'
        '"contextStrategy":{},"requiresConfirmation":false,'
        '"riskLevel":"low","estimatedCost":{},'
        '"outputSpec":{},"fallbackPlan":{}}'
    )
    contents = [
        '{"difficulty":"normal"}',
        (
            '{"clarified_goal":"task","boundary_conditions":[],'
            '"output_spec":"","examples":[],"anti_examples":[],'
            '"ambiguity_report":[]}'
        ),
        plan_content,
        (
            '{"acceptable":true,"quality_score":0.9,'
            '"issues":[],"suggestions":[]}'
        ),
    ]

    def response_events(content: str):
        async def events():
            yield SimpleNamespace(
                type="response.output_text.done",
                output_index=0,
                content_index=0,
                text=content,
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output=[SimpleNamespace(type="message")],
                    usage=None,
                    model="response-model",
                ),
            )

        return events()

    client = MagicMock()
    client.responses.create = AsyncMock(
        side_effect=[response_events(content) for content in contents]
    )
    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
        patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as execute,
    ):
        execute.return_value = "done"
        result = await run_agent(
            "task",
            registry=DefaultToolRegistry(),
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MockGateway(client, responses=True),
            toolboxes=[toolbox],
            clarifier=RequirementClarifier(interactive=False),
        )

    assert result.reply.startswith("done")
    assert client.responses.create.await_count == 4
    assert all(
        call.kwargs["stream"] is True
        for call in client.responses.create.await_args_list
    )
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_suggested_thinking_level_merges_into_llm_overrides(tmp_path) -> None:
    install_test_config(tmp_path, {"features": {"reflection": False}})
    tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

    from miniagent.agent.types.planning import StructuredPlan, SuggestedConfig

    fake_plan = StructuredPlan(
        summary="x",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(thinking_level="low"),
    )

    captured: dict[str, object] = {}

    async def _capture_exec(
        _plan: object,
        _user_input: str,
        _registry: object,
        _monitor: object,
        merged_config: object,
        *_a: object,
        **_k: object,
    ) -> str:
        captured["thinking_level"] = getattr(merged_config, "llm_overrides", {}).get(
            "thinking_level"
        )
        captured["thinking_budget"] = getattr(merged_config, "llm_overrides", {}).get(
            "thinking_budget"
        )
        return "ok"

    with (
        patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False),
    ):
        with patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp:
            gp.return_value = fake_plan
            with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex:
                ex.side_effect = _capture_exec
                await run_agent(
                    "task",
                    registry=DefaultToolRegistry(),
                    memory=make_memory_runtime(),
                    knowledge_registry=make_knowledge_registry(),
                    client=MagicMock(),
                    toolboxes=[tb],
                )

    assert captured.get("thinking_level") == "light"
    assert captured.get("thinking_budget") == 1024
