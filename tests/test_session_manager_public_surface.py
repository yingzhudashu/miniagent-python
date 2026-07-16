"""SessionManager 公共查询、磁盘恢复和工具生命周期契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from miniagent.agent.types.skill import Skill
from miniagent.agent.types.tool import Toolbox, ToolDefinition, ToolResult
from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
from miniagent.assistant.session import manager as manager_module
from miniagent.assistant.session.manager import DefaultSessionManager, _get_session_lock_owner


async def _handler(_args, _ctx) -> ToolResult:
    return ToolResult(success=True, content="ok")


def _tool(name: str = "sample") -> ToolDefinition:
    return ToolDefinition(
        schema={
            "type": "function",
            "function": {"name": name, "description": name, "parameters": {"type": "object"}},
        },
        handler=_handler,
        permission="allowlist",
        help_text=name,
        toolbox="test",
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DefaultSessionManager:
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    monkeypatch.setattr(manager_module, "_get_workspaces_dir", lambda: str(workspaces))
    return DefaultSessionManager(DefaultToolRegistry())


def test_session_identity_active_rename_and_disk_resolution(manager: DefaultSessionManager) -> None:
    first = manager.get_or_create("alpha")
    second = manager.get_or_create("beta")
    assert manager.get("alpha") is first
    assert {session.id for session in manager.list()} == {"alpha", "beta"}
    assert not manager.set_active("missing")
    assert manager.set_active("alpha") and manager.get_active_id() == "alpha"

    alpha_number = manager._sessions["alpha"]["config"].session_number
    assert manager.get_session_by_number(alpha_number)["session_id"] == "alpha"
    assert manager.resolve_session_id(str(alpha_number)) == "alpha"
    assert manager.resolve_session_id("alpha") == "alpha"
    assert manager.resolve_session_id("999999") is None
    assert manager.get_session_display_name("missing") == "missing"
    assert manager.rename_session("alpha", "新标题")
    assert "新标题" in manager.get_session_display_name("alpha")

    assert manager.forget_session("alpha")
    assert manager.get_active_id() == ""
    assert not manager.forget_session("alpha")
    assert manager.resolve_session_id(str(alpha_number)) == "alpha"
    assert manager.rename_session("alpha", "磁盘恢复")
    assert manager.get("alpha").description == "磁盘恢复"
    assert second.id == "beta"


@pytest.mark.asyncio
async def test_session_listing_destroy_and_lock_metadata(manager: DefaultSessionManager) -> None:
    session = manager.get_or_create("locked")
    workspace = Path(manager._sessions[session.id]["config"].workspace_path)
    (workspace / ".lock").write_text("123", encoding="utf-8")
    assert _get_session_lock_owner(str(workspace)) == 123
    info = manager.list_all_sessions_with_info()
    locked = next(item for item in info if item["id"] == "locked")
    assert locked["locked"] and locked["lock_pid"] == 123

    (workspace / ".lock").write_text("invalid", encoding="utf-8")
    assert _get_session_lock_owner(str(workspace)) is None
    assert await manager.delete_session("locked", keep_files=False)
    assert not workspace.exists()
    assert not await manager.delete_session("locked")


def test_tool_registration_promotion_context_and_snapshots(manager: DefaultSessionManager) -> None:
    tool = _tool()
    assert not manager.register_tool("missing", "sample", tool)
    assert not manager.unregister_tool("missing", "sample")
    missing_context = manager.get_tool_context("missing")
    assert missing_context.session_key == "missing"

    manager.get_or_create("tools")
    assert manager.get_session_files_path("missing") is None
    assert manager.get_session_files_path("tools")
    context = manager.get_tool_context("tools")
    assert context.cwd == manager.get_session_files_path("tools")
    assert manager.register_tool("tools", "sample", tool)
    assert not manager.register_tool("tools", "sample", tool)
    assert manager.promote_tool("tools", "sample")
    assert not manager.promote_tool("tools", "sample")
    assert not manager.promote_tool("missing", "sample")
    assert not manager.promote_tool("tools", "unknown")
    assert "sample" in manager.get_main_tools()
    assert manager.demote_tool("tools", "sample")
    assert manager.unregister_tool("tools", "sample")
    assert not manager.unregister_tool("tools", "sample")

    skill = Skill(id="s", name="S", description="skill")
    toolbox = Toolbox(id="tb", name="TB", description="toolbox")
    manager.refresh_main_skills([skill], [toolbox])
    assert manager.get_main_skills() == [skill]
    assert manager.get_main_toolboxes() == [toolbox]
    manager.refresh_main_skills([])
    assert manager.get_main_skills() == []
    assert manager.get_main_toolboxes() == [toolbox]
    assert manager.get_main_registry() is not None
