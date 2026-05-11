"""manage_scheduled_task 内置工具。"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from miniagent.tools.schedule_tools import _manage_scheduled_task_handler
from miniagent.types.tool import ToolContext


@pytest.fixture()
def state_dir(monkeypatch: pytest.MonkeyPatch) -> str:
    d = tempfile.mkdtemp()
    monkeypatch.setenv("MINI_AGENT_STATE", d)
    return d


@pytest.mark.asyncio
async def test_manage_scheduled_task_list_empty(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=True)
    r = await _manage_scheduled_task_handler({"action": "list"}, ctx)
    assert r.success is True
    assert "暂无" in r.content


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
async def test_manage_scheduled_task_mutations_blocked_when_feishu_mode(state_dir: str) -> None:
    ctx = ToolContext(cwd="/tmp", cli_dispatch_allow_mutations=False)
    r = await _manage_scheduled_task_handler(
        {"action": "add_interval", "task_id": "x", "prompt": "p", "interval_seconds": 60},
        ctx,
    )
    assert r.success is False
    assert "不允许" in r.content or "飞书" in r.content
