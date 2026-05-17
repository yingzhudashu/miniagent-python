"""manage_scheduled_task 内置工具。"""

from __future__ import annotations

import json
import os

import pytest

from miniagent.tools.schedule_tools import _manage_scheduled_task_handler
from miniagent.types.tool import ToolContext


@pytest.mark.asyncio
async def test_manage_scheduled_task_add_interval_roundtrip(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    r = await _manage_scheduled_task_handler(
        {
            "action": "add_interval",
            "task_id": "tool_t1",
            "prompt": "hello from tool",
            "interval_seconds": 120,
            "session_mode": "primary",
        },
        ctx,
    )
    assert r.success is True
    assert "已添加" in r.content

    path = os.path.join(state_dir, "scheduled_tasks", "tasks.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert any(t["id"] == "tool_t1" for t in data["tasks"])


@pytest.mark.asyncio
async def test_manage_scheduled_task_add_cron_roundtrip(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    r = await _manage_scheduled_task_handler(
        {
            "action": "add_cron",
            "task_id": "tool_cron1",
            "prompt": "cron job",
            "cron_expr": "15 9 * * *",
            "timezone": "UTC",
            "session_mode": "primary",
        },
        ctx,
    )
    assert r.success is True
    path = os.path.join(state_dir, "scheduled_tasks", "tasks.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    row = next(t for t in data["tasks"] if t["id"] == "tool_cron1")
    assert row["schedule"]["kind"] == "cron"
    assert row["schedule"]["cron_expr"] == "15 9 * * *"


@pytest.mark.asyncio
async def test_manage_scheduled_task_mutations_blocked_when_feishu_mode(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=False)
    r = await _manage_scheduled_task_handler(
        {"action": "add_interval", "task_id": "x", "prompt": "p", "interval_seconds": 60},
        ctx,
    )
    assert r.success is False
    assert "不允许修改定时任务" in r.content


@pytest.mark.asyncio
async def test_manage_scheduled_task_show_remove_set_enabled(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    add = await _manage_scheduled_task_handler(
        {
            "action": "add_interval",
            "task_id": "crud1",
            "prompt": "do work",
            "interval_seconds": 300,
            "session_mode": "primary",
        },
        ctx,
    )
    assert add.success is True

    show = await _manage_scheduled_task_handler({"action": "show", "task_id": "crud1"}, ctx)
    assert show.success is True
    assert '"id": "crud1"' in show.content

    off = await _manage_scheduled_task_handler(
        {"action": "set_enabled", "task_id": "crud1", "enabled": False}, ctx
    )
    assert off.success is True

    rm = await _manage_scheduled_task_handler({"action": "remove", "task_id": "crud1"}, ctx)
    assert rm.success is True
    assert "已删除" in rm.content

    path = os.path.join(state_dir, "scheduled_tasks", "tasks.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["tasks"] == []


@pytest.mark.asyncio
async def test_manage_scheduled_task_add_once_future(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    r = await _manage_scheduled_task_handler(
        {
            "action": "add_once",
            "task_id": "once1",
            "prompt": "remind",
            "once_iso": "2035-12-01T12:00:00Z",
            "timezone": "UTC",
            "session_mode": "primary",
        },
        ctx,
    )
    assert r.success is True
    assert "once1" in r.content


@pytest.mark.asyncio
async def test_manage_scheduled_task_update_prompt(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    await _manage_scheduled_task_handler(
        {
            "action": "add_interval",
            "task_id": "upd1",
            "prompt": "old",
            "interval_seconds": 120,
            "session_mode": "primary",
        },
        ctx,
    )
    r = await _manage_scheduled_task_handler(
        {
            "action": "update",
            "task_id": "upd1",
            "prompt": "new prompt",
            "interval_seconds": 180,
        },
        ctx,
    )
    assert r.success is True
    assert "已更新" in r.content
    path = os.path.join(state_dir, "scheduled_tasks", "tasks.json")
    with open(path, encoding="utf-8") as f:
        row = next(t for t in json.load(f)["tasks"] if t["id"] == "upd1")
    assert row["prompt"] == "new prompt"
    assert row["schedule"]["interval_seconds"] == 180


@pytest.mark.asyncio
async def test_manage_scheduled_task_rejects_duplicate_task_id(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    payload = {
        "action": "add_interval",
        "task_id": "dup1",
        "prompt": "p",
        "interval_seconds": 60,
        "session_mode": "primary",
    }
    r1 = await _manage_scheduled_task_handler(payload, ctx)
    assert r1.success is True
    r2 = await _manage_scheduled_task_handler(payload, ctx)
    assert r2.success is False
    assert "已存在" in r2.content


@pytest.mark.asyncio
async def test_manage_scheduled_task_unknown_action(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    r = await _manage_scheduled_task_handler({"action": "nope"}, ctx)
    assert r.success is False
    assert "未知 action" in r.content
