"""`/help` Markdown 列表输出（飞书 lark_md 友好）。"""

from __future__ import annotations

import pytest

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.engine.cli_commands import _md_help_section, format_help_markdown
from miniagent.engine.command_dispatch import _REGISTERED_COMMANDS, dispatch_command
from miniagent.engine.engine import UnifiedEngine
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
from tests.memory_helpers import (
    make_background_task_manager,
    make_knowledge_registry,
    make_memory_runtime,
)
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
    # 配置与诊断：可选参数单行描述（非重复命令行）
    assert "**`/config [section]`**" in md
    assert "**`/model [name]`**" in md
    assert "**`/config <section>`**" not in md
    assert "**`/model <model>`**" not in md
    assert "### 配置与诊断" in md
    # 实例信息
    assert "当前实例：**#7**" in md


def test_help_covers_all_registered_commands() -> None:
    """_REGISTERED_COMMANDS 中每个顶层命令均出现在 /help 输出中。"""
    md = format_help_markdown(MessageQueueManager())
    for cmd in _REGISTERED_COMMANDS:
        assert cmd in md, f"{cmd} missing from /help"
    assert "/btw clear" in md, "/btw clear missing from /help"


def test_md_help_header_separated_from_first_section() -> None:
    """元信息（当前实例）与首个分节标题之间应有可见空行（飞书 lark_md）。"""
    from miniagent.feishu.poll_server import _normalize_lark_md

    md = format_help_markdown(MessageQueueManager(), instance_id=1)
    norm = _normalize_lark_md(md)
    gap = norm.split("当前实例：**#1**", 1)[1]
    assert gap.startswith("\n\n**启动命令（在操作系统终端执行）**") or gap.startswith(
        "\n\n\n**启动命令（在操作系统终端执行）**"
    )


def test_md_help_section_title_adjacent_to_list() -> None:
    """节标题应紧贴列表（飞书 lark_md 分组）。"""
    from miniagent.feishu.poll_server import _normalize_lark_md

    s1 = _md_help_section("节A", None, [("`/cmd1`", "说明1")])
    md = _normalize_lark_md(s1)
    assert "**节A**\n- **`/cmd1`**" in md


def test_md_help_section_list_separated_from_next_title() -> None:
    """上一节列表与下一节标题之间应有足够空行。"""
    from miniagent.feishu.poll_server import _normalize_lark_md

    s1 = _md_help_section("节A", None, [("`/cmd1`", "说明1")])
    s2 = _md_help_section("节B", None, [("`/cmd2`", "说明2")])
    md = _normalize_lark_md(s1 + s2)
    assert "说明1\n\n**节B**" in md


@pytest.mark.asyncio
async def test_dispatch_help_capture_contains_list() -> None:
    """测试通过命令调度器调用 /help 返回列表格式。"""
    mq = MessageQueueManager()
    ms, al, ki, mc = _make_memory_bundle()
    ctx = ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki, context=mc),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
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
    assert "**`/help`**" in out
    assert "###" in out


@pytest.mark.asyncio
async def test_dispatch_reload_skills_slash_prefix() -> None:
    """/reload-skills 应被调度（非仅 .reload-skills）。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    mq = MessageQueueManager()
    ms, al, ki, mc = _make_memory_bundle()
    ctx = ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory=make_memory_runtime(store=ms, activity_log=al, keyword_index=ki, context=mc),
        knowledge_registry=make_knowledge_registry(),
        background_tasks=make_background_task_manager(),
    )
    state = {
        "active_session_id": "default",
        "runtime_ctx": ctx,
        "session_manager": None,
    }
    fr = MagicMock(
        package_ids=["pkg-a"],
        loaded_skills=["s1"],
        added_tools=["t1"],
        removed_tools=[],
    )
    with patch(
        "miniagent.skills.refresh.refresh_skills",
        new_callable=AsyncMock,
        return_value=fr,
    ):
        out = await dispatch_command("/reload-skills", state=state, capture=True)
    assert out is not None
    assert "技能已重新加载" in out
    assert "pkg-a" in out


def test_md_escape_cell_escapes_pipe() -> None:
    """测试表格单元格转义（仍用于 /session list 和 /queue status 的表格）。"""
    from miniagent.engine.cli_commands import _md_escape_cell

    assert _md_escape_cell("a|b") == r"a\|b"
