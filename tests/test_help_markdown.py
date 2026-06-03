"""`/help` Markdown 列表输出（飞书 lark_md 友好）。"""

from __future__ import annotations

import pytest

from miniagent.engine.cli_commands import format_help_markdown
from miniagent.engine.command_dispatch import dispatch_command
from miniagent.engine.engine import UnifiedEngine
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.runtime.context import RuntimeContext
from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
from tests.test_startup import _make_memory_bundle


def test_format_help_markdown_has_sections_and_commands() -> None:
    """测试帮助文档包含分节和命令（列表格式）。"""
    mq = MessageQueueManager()
    md = format_help_markdown(mq, instance_id=7)

    # 标题
    assert "## Mini Agent" in md
    # 分节标题
    assert "### 会话管理" in md
    # 列表格式（粗体命令）
    assert "**`/session list`**" in md
    assert "### 飞书控制" in md
    assert "**`/feishu start`**" in md
    # 实例信息
    assert "当前实例：**#7**" in md


@pytest.mark.asyncio
async def test_dispatch_help_capture_contains_list() -> None:
    """测试通过命令调度器调用 /help 返回列表格式。"""
    mq = MessageQueueManager()
    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    ctx.create_feishu_handler_factory = lambda tb, tp, st: lambda *a, **k: None

    state = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": None,
        "runtime_ctx": ctx,
        "feishu_p2p_synced_senders": set(),
    }

    out = await dispatch_command("/help", state=state, capture=True)
    assert out is not None
    # 列表格式（粗体命令）
    assert "**`/help`**" in out
    # 分节标题
    assert "###" in out


def test_md_escape_cell_escapes_pipe() -> None:
    """测试表格单元格转义（仍用于 /session list 和 /queue status 的表格）。"""
    from miniagent.engine.cli_commands import _md_escape_cell

    assert _md_escape_cell("a|b") == r"a\|b"