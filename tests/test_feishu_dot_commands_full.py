"""``feishu.dot_commands_full`` 配置与 dispatch 行为。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from miniagent.agent.monitor import DefaultToolMonitor
from miniagent.agent.tools.registry import DefaultToolRegistry
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.engine.command_dispatch import dispatch_command
from miniagent.assistant.engine.commands.session_management import feishu_dot_commands_full_enabled
from miniagent.assistant.engine.feishu_state import FeishuRuntime
from miniagent.assistant.engine.turn_service import AssistantTurnService
from miniagent.assistant.infrastructure.channel_router import ChannelRouter
from miniagent.assistant.infrastructure.message_queue import MessageQueueManager
from miniagent.assistant.skills import DefaultSkillRegistry, create_clawhub_client
from tests.config_helpers import install_test_config
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)


def _minimal_dispatch_state() -> dict:
    mq = MessageQueueManager()
    ctx = ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=AssistantTurnService(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory=make_memory_runtime(),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
    )
    return {
        "active_session_id": "keep-me",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
    ],
)
def test_feishu_dot_commands_full_enabled(
    tmp_path, value: bool, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINIAGENT_FEISHU_DOT_COMMANDS_FULL", raising=False)
    install_test_config(tmp_path, {"feishu": {"dot_commands_full": value}})
    assert feishu_dot_commands_full_enabled() is expected


def test_feishu_dot_commands_full_default_off(tmp_path) -> None:
    install_test_config(tmp_path)
    assert feishu_dot_commands_full_enabled() is False


def test_feishu_dot_commands_full_env_var(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 且无 config 时应为 True。"""
    install_test_config(tmp_path, {"feishu": {"dot_commands_full": False}})
    monkeypatch.setenv("MINIAGENT_FEISHU_DOT_COMMANDS_FULL", "1")
    assert feishu_dot_commands_full_enabled() is True


def test_feishu_dot_commands_full_string_false(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """字符串 ``\"false\"`` 应解析为关，避免 bool(\"false\") 误判。"""
    monkeypatch.delenv("MINIAGENT_FEISHU_DOT_COMMANDS_FULL", raising=False)
    with pytest.raises(ValueError, match="应为 bool"):
        install_test_config(tmp_path, {"feishu": {"dot_commands_full": "false"}})


@pytest.mark.asyncio
async def test_capture_stop_blocked_by_default(tmp_path) -> None:
    install_test_config(tmp_path)
    state = _minimal_dispatch_state()
    out = await dispatch_command("/stop", state=state, capture=True)
    assert out is not None
    assert "CLI" in out or "MINIAGENT_FEISHU_DOT_COMMANDS_FULL" in out


@pytest.mark.asyncio
async def test_capture_stop_allowed_when_full_enabled(tmp_path) -> None:
    install_test_config(tmp_path, {"feishu": {"dot_commands_full": True}})
    state = _minimal_dispatch_state()
    with patch(
        "miniagent.assistant.engine.shutdown.shutdown_runtime",
        new_callable=AsyncMock,
    ) as mock_shutdown:
        result = await dispatch_command("/stop", state=state, capture=True)
    mock_shutdown.assert_awaited_once()
    assert result == "__EXIT__"


@pytest.mark.asyncio
async def test_capture_stop_allowed_via_env_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仅 MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1、config=false 时 /stop 可执行。"""
    install_test_config(tmp_path, {"feishu": {"dot_commands_full": False}})
    monkeypatch.setenv("MINIAGENT_FEISHU_DOT_COMMANDS_FULL", "1")
    state = _minimal_dispatch_state()
    with patch(
        "miniagent.assistant.engine.shutdown.shutdown_runtime",
        new_callable=AsyncMock,
    ) as mock_shutdown:
        result = await dispatch_command("/stop", state=state, capture=True)
    mock_shutdown.assert_awaited_once()
    assert result == "__EXIT__"


@pytest.mark.asyncio
async def test_capture_schedule_mutations_blocked_by_default(tmp_path) -> None:
    install_test_config(tmp_path, {"feishu": {"dot_commands_full": False}})
    state = _minimal_dispatch_state()
    task_id = f"t_{uuid.uuid4().hex[:8]}"
    out = await dispatch_command(
        f"/schedule add {task_id} every 60 primary -- hello",
        state=state,
        capture=True,
        allow_session_mutations_when_capture=False,
    )
    assert out is not None
    assert "不允许修改定时任务" in out


@pytest.mark.asyncio
async def test_capture_schedule_mutations_allowed_when_flag_true(
    state_dir: str,
) -> None:
    from miniagent.assistant.engine.commands.session_management import cmd_schedule
    from miniagent.assistant.scheduled_tasks.store import load_tasks, save_tasks

    out = await dispatch_command(
        "/schedule add feishu_full_test every 3600 primary -- probe",
        state=_minimal_dispatch_state(),
        capture=True,
        allow_session_mutations_when_capture=True,
    )
    assert out is not None
    assert "不允许修改" not in out
    assert any(t.id == "feishu_full_test" for t in load_tasks())
    save_tasks([t for t in load_tasks() if t.id != "feishu_full_test"])
    cleanup = cmd_schedule(
        "/schedule remove feishu_full_test",
        allow_mutations=True,
    )
    assert "未找到" in cleanup


@pytest.mark.asyncio
async def test_capture_schedule_unblocked_when_full_env_only(
    tmp_path,
    state_dir: str,
) -> None:
    """仅配置 dot_commands_full=true、显式 allow=False 时仍放行 schedule 变异。"""
    from miniagent.assistant.scheduled_tasks.store import load_tasks, save_tasks

    install_test_config(tmp_path, {"feishu": {"dot_commands_full": True}})
    out = await dispatch_command(
        "/schedule add env_only_full every 3600 primary -- probe",
        state=_minimal_dispatch_state(),
        capture=True,
        allow_session_mutations_when_capture=False,
    )
    assert out is not None
    assert "不允许修改" not in out
    save_tasks([t for t in load_tasks() if t.id != "env_only_full"])


@pytest.mark.asyncio
async def test_capture_session_not_blocked_when_full_env_only(tmp_path) -> None:
    """仅配置 dot_commands_full=true、allow=False 时不返回远程会话提示。"""
    install_test_config(tmp_path, {"feishu": {"dot_commands_full": True}})
    out = await dispatch_command(
        "/session switch 1",
        state=_minimal_dispatch_state(),
        capture=True,
        allow_session_mutations_when_capture=False,
    )
    assert out is not None
    assert "共享" not in out
    assert "本地 MiniAgent 终端" not in out


def test_engine_feishu_mutations_follow_env(tmp_path) -> None:
    from miniagent.agent.config import get_default_agent_config, merge_agent_config

    install_test_config(tmp_path, {"feishu": {"dot_commands_full": True}})
    base = get_default_agent_config()
    merged = merge_agent_config(
        base,
        {"feishu_config": {"cli_dispatch_allow_mutations": feishu_dot_commands_full_enabled()}},
    )
    assert merged.feishu_config.cli_dispatch_allow_mutations is True

    install_test_config(tmp_path)
    merged_off = merge_agent_config(
        base,
        {"feishu_config": {"cli_dispatch_allow_mutations": feishu_dot_commands_full_enabled()}},
    )
    assert merged_off.feishu_config.cli_dispatch_allow_mutations is False
