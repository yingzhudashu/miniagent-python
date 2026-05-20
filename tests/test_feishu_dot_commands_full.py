"""MINIAGENT_FEISHU_DOT_COMMANDS_FULL 环境变量与 dispatch 行为。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from miniagent.engine.cli_commands import feishu_dot_commands_full_enabled
from miniagent.engine.command_dispatch import dispatch_command
from miniagent.engine.engine import UnifiedEngine
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.runtime.context import RuntimeContext
from miniagent.skills import DefaultSkillRegistry, create_clawhub_client


def _minimal_dispatch_state() -> dict:
    mq = MessageQueueManager()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=None,
        activity_log=None,
        keyword_index=None,
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
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("no", False),
    ],
)
def test_feishu_dot_commands_full_enabled(value: str, expected: bool) -> None:
    key = "MINIAGENT_FEISHU_DOT_COMMANDS_FULL"
    old = os.environ.get(key)
    try:
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
        assert feishu_dot_commands_full_enabled() is expected
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def test_feishu_dot_commands_full_default_off() -> None:
    key = "MINIAGENT_FEISHU_DOT_COMMANDS_FULL"
    old = os.environ.pop(key, None)
    try:
        assert feishu_dot_commands_full_enabled() is False
    finally:
        if old is not None:
            os.environ[key] = old


@pytest.mark.asyncio
async def test_capture_stop_blocked_by_default() -> None:
    key = "MINIAGENT_FEISHU_DOT_COMMANDS_FULL"
    old = os.environ.pop(key, None)
    try:
        state = _minimal_dispatch_state()
        out = await dispatch_command(".stop", state=state, capture=True)
        assert out is not None
        assert "CLI" in out or "MINIAGENT_FEISHU_DOT_COMMANDS_FULL" in out
    finally:
        if old is not None:
            os.environ[key] = old


@pytest.mark.asyncio
async def test_capture_stop_allowed_when_full_enabled() -> None:
    key = "MINIAGENT_FEISHU_DOT_COMMANDS_FULL"
    old = os.environ.get(key)
    os.environ[key] = "1"
    state = _minimal_dispatch_state()
    try:
        with patch(
            "miniagent.engine.shutdown.shutdown_runtime",
            new_callable=AsyncMock,
        ) as mock_shutdown:
            with patch("miniagent.engine.command_dispatch.sys.exit") as mock_exit:
                await dispatch_command(".stop", state=state, capture=True)
        mock_shutdown.assert_awaited_once()
        mock_exit.assert_called_once_with(0)
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@pytest.mark.asyncio
async def test_capture_schedule_mutations_blocked_by_default() -> None:
    state = _minimal_dispatch_state()
    out = await dispatch_command(
        ".schedule add x every 60 primary -- hello",
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
    from miniagent.engine.cli_commands import cmd_schedule
    from miniagent.scheduled_tasks.store import load_tasks, save_tasks

    out = await dispatch_command(
        ".schedule add feishu_full_test every 3600 primary -- probe",
        state=_minimal_dispatch_state(),
        capture=True,
        allow_session_mutations_when_capture=True,
    )
    assert out is not None
    assert "不允许修改" not in out
    assert any(t.id == "feishu_full_test" for t in load_tasks())
    save_tasks([t for t in load_tasks() if t.id != "feishu_full_test"])
    cleanup = cmd_schedule(
        ".schedule remove feishu_full_test",
        allow_mutations=True,
    )
    assert "未找到" in cleanup


@pytest.mark.asyncio
async def test_capture_schedule_unblocked_when_full_env_only(
    state_dir: str,
) -> None:
    """仅 env FULL=1、显式 allow=False 时仍放行 schedule 变异（block_remote 读 env）。"""
    from miniagent.scheduled_tasks.store import load_tasks, save_tasks

    key = "MINIAGENT_FEISHU_DOT_COMMANDS_FULL"
    old = os.environ.get(key)
    os.environ[key] = "1"
    try:
        out = await dispatch_command(
            ".schedule add env_only_full every 3600 primary -- probe",
            state=_minimal_dispatch_state(),
            capture=True,
            allow_session_mutations_when_capture=False,
        )
        assert out is not None
        assert "不允许修改" not in out
        save_tasks([t for t in load_tasks() if t.id != "env_only_full"])
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@pytest.mark.asyncio
async def test_capture_session_not_blocked_when_full_env_only() -> None:
    """仅 env FULL=1、allow=False 时不返回远程会话提示。"""
    key = "MINIAGENT_FEISHU_DOT_COMMANDS_FULL"
    old = os.environ.get(key)
    os.environ[key] = "1"
    try:
        out = await dispatch_command(
            ".session switch 1",
            state=_minimal_dispatch_state(),
            capture=True,
            allow_session_mutations_when_capture=False,
        )
        assert out is not None
        assert "共享" not in out
        assert "本地 MiniAgent 终端" not in out
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def test_engine_feishu_mutations_follow_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.core.config import get_default_agent_config, merge_agent_config

    monkeypatch.setenv("MINIAGENT_FEISHU_DOT_COMMANDS_FULL", "1")
    base = get_default_agent_config()
    merged = merge_agent_config(
        base,
        {"cli_dispatch_allow_mutations": feishu_dot_commands_full_enabled()},
    )
    assert merged.cli_dispatch_allow_mutations is True

    monkeypatch.delenv("MINIAGENT_FEISHU_DOT_COMMANDS_FULL", raising=False)
    merged_off = merge_agent_config(
        base,
        {"cli_dispatch_allow_mutations": feishu_dot_commands_full_enabled()},
    )
    assert merged_off.cli_dispatch_allow_mutations is False
