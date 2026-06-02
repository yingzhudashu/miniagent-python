"""run_dot_command 工具与 merge_agent_config 新字段。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.tools.cli_dispatch_tools import _run_dot_command_handler
from miniagent.types.tool import ToolContext


def test_merge_agent_config_cli_loop_fields() -> None:
    state = {"runtime_ctx": MagicMock()}
    base = get_default_agent_config()
    merged = merge_agent_config(
        base,
        {
            "cli_loop_state": state,
            "cli_dispatch_allow_mutations": False,
            "feishu_receive_chat_id": "oc_x",
            "feishu_im_receive_id_type": "open_id",
            "feishu_im_receive_id": "ou_sender",
        },
    )
    assert merged.cli_loop_state is state
    assert merged.cli_dispatch_allow_mutations is False
    assert merged.feishu_receive_chat_id == "oc_x"
    assert merged.feishu_im_receive_id_type == "open_id"
    assert merged.feishu_im_receive_id == "ou_sender"


@pytest.mark.asyncio
async def test_run_dot_command_requires_dot_prefix() -> None:
    rt = MagicMock()
    st = {"runtime_ctx": rt}
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st)
    r = await _run_dot_command_handler({"line": "help"}, ctx)
    assert r.success is False
    assert "." in r.content


@pytest.mark.asyncio
async def test_run_dot_command_no_runtime_ctx_fails() -> None:
    ctx = ToolContext(cwd="/tmp", cli_loop_state=None)
    r = await _run_dot_command_handler({"line": ".help"}, ctx)
    assert r.success is False
    assert "runtime_ctx" in r.content

    ctx2 = ToolContext(cwd="/tmp", cli_loop_state={})
    r2 = await _run_dot_command_handler({"line": ".help"}, ctx2)
    assert r2.success is False


@pytest.mark.asyncio
async def test_run_dot_command_dispatches_capture_and_allow_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_dispatch(
        text: str,
        *,
        state,
        engine,
        registry,
        monitor,
        skill_toolboxes,
        skill_prompts,
        capture,
        allow_session_mutations_when_capture,
        feishu_user_status,
        message_queue_abort_chat_id=None,
    ):
        captured["text"] = text
        captured["state"] = state
        captured["capture"] = capture
        captured["allow"] = allow_session_mutations_when_capture
        captured["feishu_user_status"] = feishu_user_status
        captured["message_queue_abort_chat_id"] = message_queue_abort_chat_id
        return "mocked"

    monkeypatch.setattr(
        "miniagent.engine.command_dispatch.dispatch_command",
        fake_dispatch,
    )

    rt = MagicMock()
    rt.engine = "eng"
    rt.registry = "reg"
    rt.monitor = "mon"
    st = {
        "runtime_ctx": rt,
        "skill_toolboxes": ["tb"],
        "skill_prompts": ["sp"],
    }
    ctx = ToolContext(
        cwd="/tmp",
        cli_loop_state=st,
        cli_dispatch_allow_mutations=False,
        message_queue_abort_chat_id="oc_tool_room",
    )
    r = await _run_dot_command_handler({"line": "  .status  "}, ctx)
    assert r.success is True
    assert r.content == "mocked"
    assert captured["text"] == ".status"
    assert captured["state"] is st
    assert captured["capture"] is True
    assert captured["allow"] is False
    assert captured["feishu_user_status"] is None
    assert captured["message_queue_abort_chat_id"] == "oc_tool_room"


@pytest.mark.asyncio
async def test_run_dot_command_empty_capture_becomes_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_dispatch(*_a, **_k):
        return "   \n  "

    monkeypatch.setattr(
        "miniagent.engine.command_dispatch.dispatch_command",
        fake_dispatch,
    )
    rt = MagicMock()
    st = {"runtime_ctx": rt}
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st)
    r = await _run_dot_command_handler({"line": ".noop"}, ctx)
    assert r.success is True
    assert "无文本输出" in r.content


@pytest.mark.asyncio
async def test_run_dot_command_status_real_dispatch_minimal_state() -> None:
    """不 patch dispatch_command：仅 .status 所需字段为 MagicMock。"""
    mq = MagicMock()
    mq.get_status = MagicMock(return_value={"mode": "queue", "chats": {}})
    cr = MagicMock()
    cr.get_all_bindings = MagicMock(return_value={})
    feishu = MagicMock()
    feishu.is_running = MagicMock(return_value=False)
    rt = MagicMock()
    rt.message_queue = mq
    rt.channel_router = cr
    rt.feishu = feishu
    rt.engine = None
    rt.registry = None
    rt.monitor = None

    st = {
        "runtime_ctx": rt,
        "skill_toolboxes": [],
        "skill_prompts": [],
        "instance_id": 0,
        "active_session_id": "",
        "session_manager": None,
    }
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st, cli_dispatch_allow_mutations=True)
    r = await _run_dot_command_handler({"line": ".status"}, ctx)
    assert r.success is True
    assert "消息队列" in r.content
    assert "飞书" in r.content


@pytest.mark.asyncio
async def test_run_dot_command_max_chars_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long = "x" * 500

    async def fake_dispatch(*_a, **_k):
        return long

    monkeypatch.setattr(
        "miniagent.engine.command_dispatch.dispatch_command",
        fake_dispatch,
    )
    rt = MagicMock()
    st = {"runtime_ctx": rt}
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st)
    r = await _run_dot_command_handler({"line": ".x", "max_chars": 100}, ctx)
    assert r.success is True
    assert len(r.content) < len(long)
    assert "截断" in r.content
    assert r.content.startswith("x" * 100)


@pytest.mark.asyncio
@pytest.mark.dot_help_dispatch
async def test_run_dot_command_help_real_dispatch_minimal_state() -> None:
    """不 patch dispatch_command：.help 需 message_queue.mode.value。"""
    mq = SimpleNamespace(mode=SimpleNamespace(value="queue"))
    cr = MagicMock()
    cr.get_all_bindings = MagicMock(return_value={})
    feishu = MagicMock()
    feishu.is_running = MagicMock(return_value=False)
    rt = MagicMock()
    rt.message_queue = mq
    rt.channel_router = cr
    rt.feishu = feishu
    rt.engine = None
    rt.registry = None
    rt.monitor = None

    st = {
        "runtime_ctx": rt,
        "skill_toolboxes": [],
        "skill_prompts": [],
        "instance_id": None,
        "active_session_id": "",
        "session_manager": None,
    }
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st, cli_dispatch_allow_mutations=True)
    r = await _run_dot_command_handler({"line": ".help"}, ctx)
    assert r.success is True
    assert "Mini Agent" in r.content or "命令" in r.content


@pytest.mark.asyncio
async def test_run_dot_command_schedule_list_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tempfile

    d = tempfile.mkdtemp()
    monkeypatch.setenv("MINIAGENT_PATHS_STATE_DIR", d)
    mq = SimpleNamespace(mode=SimpleNamespace(value="queue"))
    cr = MagicMock()
    cr.get_all_bindings = MagicMock(return_value={})
    feishu = MagicMock()
    feishu.is_running = MagicMock(return_value=False)
    rt = MagicMock()
    rt.message_queue = mq
    rt.channel_router = cr
    rt.feishu = feishu
    rt.engine = None
    rt.registry = None
    rt.monitor = None

    st = {
        "runtime_ctx": rt,
        "skill_toolboxes": [],
        "skill_prompts": [],
        "instance_id": None,
        "active_session_id": "",
        "session_manager": None,
    }
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st, cli_dispatch_allow_mutations=True)
    r = await _run_dot_command_handler({"line": ".schedule list"}, ctx)
    assert r.success is True
    assert "暂无" in r.content or "定时任务" in r.content


@pytest.mark.asyncio
async def test_run_dot_command_unknown_returns_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_dispatch(*_a, **_k):
        return None

    monkeypatch.setattr(
        "miniagent.engine.command_dispatch.dispatch_command",
        fake_dispatch,
    )
    rt = MagicMock()
    st = {"runtime_ctx": rt}
    ctx = ToolContext(cwd="/tmp", cli_loop_state=st)
    r = await _run_dot_command_handler({"line": ".not_a_real_cmd_xyz"}, ctx)
    assert r.success is False
