"""新分层模块的错误、缓存、格式化与兼容边界。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from miniagent.agent import agent_display, planner_support
from miniagent.agent.types.config import AgentConfig, SessionBindingConfig
from miniagent.agent.types.planning import (
    ContextStrategy,
    EstimatedCost,
    OutputSpec,
    PlanChunk,
    PlanStep,
    StructuredPlan,
)
from miniagent.assistant.infrastructure import http_retry, instance_render
from miniagent.llm import capabilities as llm_capabilities


class _CapabilityClient:
    """可弱引用的能力缓存测试客户端。"""

    def __init__(self, base_url: str = "https://gateway.example") -> None:
        self.base_url = base_url


class _GatewayError(Exception):
    """携带兼容 HTTP 状态码的供应商错误。"""

    def __init__(self, message: str, status_code: int | str) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_llm_capability_detection_weak_and_fallback_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_capabilities._CLIENT_BUCKETS.clear()
    llm_capabilities._FALLBACK.clear()
    assert llm_capabilities.unsupported_parameter_names(_GatewayError("bad", 500)) == set()
    assert llm_capabilities.unsupported_parameter_names(
        _GatewayError("temperature parameter is not supported", "400")
    ) == {"temperature"}

    events: list[dict[str, object]] = []
    monkeypatch.setattr("miniagent.agent.observability.emit_trace", events.append)
    client = _CapabilityClient()
    params = {"model": "m", "temperature": 0.2, "top_p": 0.9, "keep": True}
    llm_capabilities.learn_unsupported_params(
        client,
        params,
        "responses",
        _GatewayError("unsupported parameter: temperature", 400),
    )
    adjusted, removed = llm_capabilities.apply_learned_capabilities(
        client, params, "responses"
    )
    assert removed == ("temperature",)
    assert adjusted == {"model": "m", "top_p": 0.9, "keep": True}
    assert events == []  # LLM capability learning stays independent from Agent tracing.

    fallback_client: object = object()
    llm_capabilities.learn_unsupported_params(
        fallback_client,
        params,
        "chat_completions",
        _GatewayError("top_p is not supported", 400),
    )
    adjusted, removed = llm_capabilities.apply_learned_capabilities(
        fallback_client, params, "chat_completions"
    )
    assert removed == ("top_p",) and "top_p" not in adjusted
    untouched, removed = llm_capabilities.apply_learned_capabilities(
        _CapabilityClient("other"), params, "responses"
    )
    assert untouched is params and removed == ()


def test_llm_capability_buckets_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_capabilities._CLIENT_BUCKETS.clear()
    llm_capabilities._FALLBACK.clear()
    monkeypatch.setattr(llm_capabilities, "_CLIENT_MAX", 1)
    monkeypatch.setattr(llm_capabilities, "_FALLBACK_MAX", 1)
    error = _GatewayError("temperature not supported", 400)
    client = _CapabilityClient()
    for model in ("first", "second"):
        llm_capabilities.learn_unsupported_params(
            client, {"model": model, "temperature": 1}, "responses", error
        )
    assert len(llm_capabilities._CLIENT_BUCKETS[client]) == 1
    for fallback in (object(), object()):
        llm_capabilities.learn_unsupported_params(
            fallback, {"model": "m", "temperature": 1}, "responses", error
        )
    assert len(llm_capabilities._FALLBACK) == 1


def _response(status: int, text: str = "ok") -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("GET", "https://x"))


@pytest.mark.asyncio
async def test_http_retry_methods_success_and_json_helpers() -> None:
    post = AsyncMock(return_value=_response(200, '{"post": true}'))
    get = AsyncMock(return_value=_response(200, '{"get": true}'))
    request = AsyncMock(return_value=_response(200, "custom"))
    client = SimpleNamespace(post=post, get=get, request=request)

    assert await http_retry.async_http_post_json_with_retry(
        client, "https://x", payload={"x": 1}, headers={"A": "B"}, max_retries=1
    ) == {"post": True}
    assert await http_retry.async_http_get_json_with_retry(
        client, "https://x", headers={"A": "B"}, max_retries=1
    ) == {"get": True}
    response = await http_retry.async_http_request_with_retry(
        client, "PATCH", "https://x", timeout=2, max_retries=1
    )
    assert response.text == "custom"
    request.assert_awaited_once_with("PATCH", "https://x", timeout=2)


@pytest.mark.asyncio
async def test_http_retry_status_timeout_network_and_zero_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(http_retry.asyncio, "sleep", sleep)
    bad_request = SimpleNamespace(
        get=AsyncMock(return_value=_response(400, "bad")),
        post=AsyncMock(),
        request=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await http_retry.async_http_request_with_retry(
            bad_request, "GET", "https://x", max_retries=2
        )
    sleep.assert_not_awaited()

    server = SimpleNamespace(
        get=AsyncMock(side_effect=[_response(503, "busy"), _response(200)]),
        post=AsyncMock(),
        request=AsyncMock(),
    )
    assert (await http_retry.async_http_request_with_retry(
        server, "GET", "https://x", max_retries=2, backoff_factor=0
    )).status_code == 200
    with pytest.raises(RuntimeError, match="重试1次后"):
        await http_retry.async_http_request_with_retry(
            SimpleNamespace(get=AsyncMock(return_value=_response(500)), post=AsyncMock(), request=AsyncMock()),
            "GET", "https://x", max_retries=1,
        )

    for error, fragment in (
        (httpx.TimeoutException("late"), "请求超时"),
        (httpx.RequestError("offline"), "网络请求失败"),
    ):
        client = SimpleNamespace(get=AsyncMock(side_effect=error), post=AsyncMock(), request=AsyncMock())
        with pytest.raises(RuntimeError, match=fragment):
            await http_retry.async_http_request_with_retry(
                client, "GET", "https://x", max_retries=1
            )
    with pytest.raises(RuntimeError, match="未执行任何请求"):
        await http_retry.async_http_request_with_retry(
            bad_request, "GET", "https://x", max_retries=0
        )


def test_instance_render_empty_single_and_multi_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.paths.resolve_registry_state_dir", lambda: "C:/state"
    )
    monkeypatch.setattr(instance_render.os, "getpid", lambda: 10)
    assert "暂无运行实例" in instance_render.format_instances_markdown([])
    assert "暂无运行实例" in instance_render.format_instances_table([])

    items = [
        {
            "instance_id": 1, "pid": 10, "mode": "both", "project_dir": "C:/a/project",
            "project_key": "p1", "start_time": "2026-01-01T01:02:03Z",
            "active_sessions": ["s"], "hostname": "host|one", "state_dir": "C:/state",
        },
        {
            "instance_id": 2, "pid": 20, "mode": "cli", "cwd": "D:/b/project",
            "project_state_dir": "D:/state/projects/p2", "start_time": "?",
            "active_sessions": [], "hostname": "host\ntwo", "state_dir": "D:/other",
        },
    ]
    markdown = instance_render.format_instances_markdown(items)
    assert "projects/p1" in markdown and "projects/p2" in markdown
    assert "host\\|one" in markdown and "当前" in markdown and "状态目录" in markdown
    table = instance_render.format_instances_table(items)
    assert "canonical" in table and "← 当前" in table and "状态目录" in table


def test_agent_display_and_planner_support_branches() -> None:
    step = PlanStep(
        step_number=1,
        description="do",
        required_toolboxes=["fs"],
        expected_input="input",
        expected_output="output",
    )
    plan = StructuredPlan(
        summary="summary",
        steps=[step],
        required_toolboxes=["fs"],
        estimated_cost=EstimatedCost(total_usd=0.5),
        output_spec=OutputSpec(language="zh-CN", format="markdown", expected_deliverable="report"),
        context_strategy=ContextStrategy(
            mode="chunked", reason="large", chunks=[PlanChunk(chunk_number=1, steps=[step])]
        ),
    )
    assert "简单" in agent_display.format_task_difficulty("simple", display=True)
    assert "思考深度" in agent_display.format_task_difficulty("unknown")
    short = agent_display.format_plan_display_short(plan, from_llm_planner=True)
    full = agent_display.format_plan_message(plan, from_llm_planner=True)
    assert "预估成本" in short and "工具箱" in short
    assert all(part in full for part in ("预期输入", "预期产出", "上下文策略", "分 1 块"))
    for flags, fragment in (
        ({"no_toolboxes": True}, "无可用工具箱"),
        ({"user_skip_planning": True}, "显式跳过"),
        ({"simple_classified": True}, "简单"),
        ({}, "未调用"),
    ):
        rendered = agent_display.format_plan_display_short(
            plan, from_llm_planner=False, **flags
        )
        assert fragment in rendered

    config = AgentConfig(
        session_config=SessionBindingConfig(
            conversation_history=[
                {"role": "assistant", "content": "已完成 pytest"},
                {"role": "assistant", "content": "unrelated"},
            ]
        )
    )
    assert "已完成 pytest" in planner_support.completed_work_context(config)
    assert planner_support.completed_work_context(None) == ""
    registry = SimpleNamespace(get_all=lambda: {
        "read": SimpleNamespace(toolbox=None),
        "search": SimpleNamespace(toolbox="web"),
    })
    mapping = planner_support.format_toolbox_tool_names(registry, ["web", "empty"])
    assert "__core__" in mapping and "search" in mapping and "无匹配工具" in mapping
    assert planner_support.format_toolbox_tool_names(
        SimpleNamespace(get_all=lambda: (_ for _ in ()).throw(RuntimeError())), ["web"]
    ) == ""
    fallback = planner_support.fallback_plan("request")
    assert fallback.steps[0].expected_input == "request" and not fallback.fallback_plan.degrade_to_simple


@pytest.mark.asyncio
async def test_runtime_service_start_updates_state_and_starts_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.bootstrap.runtime_services as runtime_services
    from miniagent.assistant.engine import init as init_module
    from miniagent.assistant.engine import main, parallel_config

    session_manager = object()
    monkeypatch.setattr(
        init_module,
        "init_subsystems",
        AsyncMock(return_value=(object(), ["toolbox"], ["prompt"], "session", session_manager)),
    )
    configure = MagicMock()
    monkeypatch.setattr(parallel_config, "configure_message_queue_for_parallel", configure)
    lifecycle = SimpleNamespace(start=AsyncMock())
    builder = MagicMock(return_value=lifecycle)
    monkeypatch.setattr(runtime_services, "build_runtime_lifecycle_manager", builder)
    engine = SimpleNamespace(set_active_session_key=MagicMock())
    ctx = SimpleNamespace(
        registry=object(), skill_registry=object(), channel_router=object(), clawhub=object(),
        memory=SimpleNamespace(keyword_index=object()), message_queue=object(), engine=engine,
        lifecycle_manager=None, cli_transcript_append=None,
    )
    state = {
        "active_session_id": "", "skill_toolboxes": [], "skill_prompts": [],
        "feishu_enabled": False, "session_manager": None, "instance_id": 1,
        "runtime_ctx": ctx, "feishu_p2p_synced_senders": set(),
    }
    result = await main._start_runtime_services(ctx, state)
    assert result == (["toolbox"], ["prompt"], "session")
    assert state["session_manager"] is session_manager and ctx.lifecycle_manager is lifecycle
    configure.assert_called_once_with(ctx.message_queue)
    engine.set_active_session_key.assert_called_once_with("session")
    lifecycle.start.assert_awaited_once()


def test_runtime_initial_state_conflict_and_windows_vt_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from miniagent.assistant.engine import main

    monkeypatch.setattr(main, "register_instance", lambda **_kwargs: {"instance_id": 7})
    state = main._initial_runtime_state(SimpleNamespace(), True)
    assert state["instance_id"] == 7 and state["feishu_enabled"] is True

    conflict = main.ProjectDirConflictError({"pid": 1, "project_dir": "x"})
    monkeypatch.setattr(main, "register_instance", MagicMock(side_effect=conflict))
    monkeypatch.setattr(main, "format_project_conflict_message", lambda _meta: "conflict")
    with pytest.raises(SystemExit) as exc:
        main._initial_runtime_state(SimpleNamespace(), False)
    assert exc.value.code == 2 and "conflict" in capsys.readouterr().out

    import ctypes

    class _BrokenWindll:
        @property
        def kernel32(self):
            raise RuntimeError("no console")

    monkeypatch.setattr(ctypes, "windll", _BrokenWindll(), raising=False)
    main._enable_windows_vt()


def test_docx_rendered_table_and_block_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.feishu.docx.markdown_renderer as renderer
    import miniagent.assistant.feishu.docx.tables as tables
    from miniagent.assistant.feishu.docx import blocks
    from miniagent.ui.feishu.types import FeishuConfig

    warnings: list[str] = []
    empty = SimpleNamespace(table_data=[])
    zero_columns = SimpleNamespace(table_data=[[]])
    valid = SimpleNamespace(
        table_data=[
            [SimpleNamespace(content="a"), SimpleNamespace(content="b")],
            [SimpleNamespace(content="c")],
        ]
    )
    failed = SimpleNamespace(table_data=[[SimpleNamespace(content="x")]])
    create = MagicMock(side_effect=[None, RuntimeError("table failed")])
    monkeypatch.setattr(tables, "create_table_with_values", create)
    success, failure = blocks._append_rendered_tables(
        FeishuConfig("a", "b"), "doc", [empty, zero_columns, valid, failed], warnings
    )
    assert (success, failure) == (1, 1)
    assert create.call_args_list[0].kwargs["values"] == [["a", "b"], ["c", ""]]
    assert "table failed" in warnings[0]

    assert blocks._append_rendered_blocks(FeishuConfig("a", "b"), "doc", [], warnings) == 0
    monkeypatch.setattr(renderer, "build_lark_blocks_from_intermediate", lambda _items: [])
    assert blocks._append_rendered_blocks(
        FeishuConfig("a", "b"), "doc", [object()], warnings
    ) == 0
    monkeypatch.setattr(renderer, "build_lark_blocks_from_intermediate", lambda _items: ["block"])
    monkeypatch.setattr(blocks, "_batch_create_blocks", lambda *_args: (1, ["warning"]))
    assert blocks._append_rendered_blocks(
        FeishuConfig("a", "b"), "doc", [object()], warnings
    ) == 1
    assert warnings[-1] == "warning"


@pytest.mark.asyncio
async def test_agent_reflection_cache_and_disabled_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.agent import agent

    monkeypatch.setattr(agent, "get_config", lambda *_args, **_kwargs: False)
    assert await agent._reflect_agent_reply(
        "q", "reply", knowledge_registry=object(), client=object(),
        session_key="s", engine=object(),
    ) == "reply"

    monkeypatch.setattr(agent, "get_config", lambda *_args, **_kwargs: True)
    reflection = SimpleNamespace(score=1)
    monkeypatch.setattr(agent, "reflect_on_result", AsyncMock(return_value=reflection))
    monkeypatch.setattr(agent, "build_reflection_footer", lambda _reflection: " footer")
    engine = SimpleNamespace(_last_reflection=None)
    result = await agent._reflect_agent_reply(
        "q", "reply", knowledge_registry=object(), client=object(),
        session_key=None, engine=engine,
    )
    assert result == "reply footer" and engine._last_reflection["default"] is reflection


@pytest.mark.asyncio
async def test_agent_clarification_answer_and_failure_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.agent import agent
    from miniagent.agent.task_classifier import TaskDifficulty

    thinking = AsyncMock()
    channel = SimpleNamespace(
        request_confirmation=AsyncMock(
            return_value=SimpleNamespace(rejected=False, adjustment="answer")
        )
    )

    async def clarify(_input, *, ask_user, **_kwargs):
        assert await ask_user("question") == "answer"
        return SimpleNamespace(clarified_goal="clear")

    clarifier = SimpleNamespace(
        clarify=clarify,
        to_system_prompt=lambda _result: "clarified prompt",
    )
    monkeypatch.setattr(agent, "_announce_difficulty_and_plan_enabled", lambda: True)
    result = await agent._clarify_user_input(
        "input", difficulty=TaskDifficulty.NORMAL, clarifier=clarifier,
        confirmation_channel=channel, on_thinking=thinking,
        knowledge_registry=object(), memory=SimpleNamespace(store=object()),
        client=object(), session_key="s",
    )
    assert result.endswith("clarified prompt") and thinking.await_count >= 3

    failing = SimpleNamespace(clarify=AsyncMock(side_effect=RuntimeError("bad")))
    assert await agent._clarify_user_input(
        "original", difficulty=TaskDifficulty.NORMAL, clarifier=failing,
        confirmation_channel=None, on_thinking=None, knowledge_registry=object(),
        memory=SimpleNamespace(store=object()), client=object(), session_key="s",
    ) == "original"


@pytest.mark.asyncio
async def test_agent_high_risk_plan_cancel_with_thinking_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miniagent.agent import agent
    from miniagent.agent.task_classifier import TaskDifficulty

    plan = StructuredPlan(summary="risk", requires_confirmation=True)
    monkeypatch.setattr(agent, "generate_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(agent, "invoke_on_thinking", AsyncMock(side_effect=RuntimeError("sink")))
    on_plan = AsyncMock(
        return_value=SimpleNamespace(plan_action=lambda: ("cancel", None))
    )
    prepared, _config, from_llm, reply = await agent._prepare_plan(
        "input", toolboxes=[SimpleNamespace(id="tb")], skip_planning=False,
        difficulty=TaskDifficulty.NORMAL, config=AgentConfig(), registry=object(),
        knowledge_registry=object(), client=object(), on_plan=on_plan,
        on_thinking=AsyncMock(), session_key="s",
    )
    assert prepared is None and from_llm and "已取消" in (reply or "")
