"""执行器 system prompt 合并与规划 JSON 解析。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.executor import (
    build_current_turn_user_context,
    build_stable_execution_system_prompt,
    execute_plan,
)
from miniagent.agent.llm_json import parse_llm_json_response
from miniagent.agent.planner import _format_toolbox_tool_names
from miniagent.agent.types.planning import StructuredPlan
from miniagent.agent.types.tool import ToolDefinition
from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime
from tests.mock_strategies import (
    agent_config_with_session,
    make_ping_tool_registry,
    mock_memory_bundle,
    mock_streaming_client,
)


def test_build_stable_execution_system_prompt_cache_prefix() -> None:
    s = build_stable_execution_system_prompt(
        agent_identity="ID",
        caller_system_prompt="SKILL",
    )
    assert s.index("ID") < s.index("SKILL")
    assert "文件与工具路径规则" in s
    assert "当前进程时区" in s
    assert "TASK" not in s
    assert "KW" not in s
    assert "KB" not in s
    assert "本地时间" not in s


def test_build_current_turn_user_context_contains_volatile_parts(tmp_path) -> None:
    root = str(tmp_path / "files")
    s = build_current_turn_user_context(
        user_input="USER",
        plan_summary="TASK",
        keyword_context="KW",
        kb_context="KB",
        session_files_root=root,
        risk_level="high",
        current_time_context="NOW",
    )
    assert "用户请求：\nUSER" in s
    assert "执行计划摘要：\nTASK" in s
    assert "相关记忆：\nKW" in s
    assert "相关知识库：\nKB" in s
    assert "本任务风险等级：\nhigh" in s
    assert "当前时间上下文：\nNOW" in s
    assert str(tmp_path) in s


@pytest.mark.asyncio
async def test_execute_plan_messages_are_cache_friendly(tmp_path) -> None:
    main, sess = make_ping_tool_registry()
    mock_client = mock_streaming_client(final_text="done")
    captured: list[dict] = []
    orig = mock_client.chat.completions.create

    async def capture_kwargs(*args, **kwargs):
        captured.append(kwargs)
        return await orig(*args, **kwargs)

    mock_client.chat.completions.create = AsyncMock(side_effect=capture_kwargs)
    ms, al, ki = mock_memory_bundle()
    cfg = agent_config_with_session(sess, max_turns=1)
    cfg.session_config.session_key = None
    cfg.session_config.conversation_history = [{"role": "assistant", "content": "OLD"}]
    cfg.session_config.session_workspace = str(tmp_path / "files")
    cfg.risk_level = "medium"
    plan = StructuredPlan(summary="PLAN_SUMMARY", steps=[], required_toolboxes=[])

    with patch("miniagent.agent.knowledge.retrieve_knowledge_context", return_value="KB_CTX"):
        await execute_plan(
            plan,
            "USER_INPUT",
            main,
            MagicMock(),
            cfg,
            system_prompt="SKILL_PROMPT",
            client=mock_client,
            memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki),
            knowledge_registry=make_knowledge_registry(),
        )

    messages = captured[0]["messages"]
    assert [m["role"] for m in messages[:3]] == ["system", "assistant", "user"]
    system_content = messages[0]["content"]
    current_user = messages[-1]["content"]
    assert "SKILL_PROMPT" in system_content
    assert "PLAN_SUMMARY" not in system_content
    assert "KB_CTX" not in system_content
    assert "medium" not in system_content
    assert "本地时间" not in system_content
    assert "OLD" in messages[1]["content"]
    assert "USER_INPUT" in current_user
    assert "PLAN_SUMMARY" in current_user
    assert "KB_CTX" in current_user
    assert "medium" in current_user
    assert "本地时间" in current_user


def test_parse_llm_json_response_brace_slice() -> None:
    raw = '说明文字\n{"summary":"x","steps":[],"requiredToolboxes":[]}\n尾部'
    data = parse_llm_json_response(raw)
    assert data["summary"] == "x"
    assert data["requiredToolboxes"] == []


def test_format_toolbox_tool_names() -> None:
    reg = DefaultToolRegistry()

    async def _h(args, ctx):
        from miniagent.agent.types.tool import ToolResult

        return ToolResult(True, "")

    reg.register(
        "read_file",
        ToolDefinition(
            schema={"type": "function", "function": {"name": "read_file", "parameters": {}}},
            handler=_h,
            permission="sandbox",
            help_text="",
            toolbox="file_read",
        ),
    )
    reg.register(
        "noop",
        ToolDefinition(
            schema={"type": "function", "function": {"name": "noop", "parameters": {}}},
            handler=_h,
            permission="sandbox",
            help_text="",
            toolbox=None,
        ),
    )
    hint = _format_toolbox_tool_names(reg, ["file_read"])
    assert "read_file" in hint
    assert "__core__" in hint
    assert "noop" in hint
