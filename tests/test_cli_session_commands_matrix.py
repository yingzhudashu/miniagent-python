"""CLI 会话命令的解析、锁冲突、创建、重命名和删除矩阵。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent.assistant.engine import cli_commands


class _Manager:
    def __init__(self) -> None:
        self.sessions = {
            "one": {"id": "one", "number": 1, "title": "One", "turn_count": 2, "locked": False, "lock_pid": None},
            "two": {"id": "two", "number": 2, "title": "Two|Pipe", "turn_count": 1, "locked": True, "lock_pid": 999},
        }
        self.destroy_result = True

    def resolve_session_id(self, value):
        if value == "1":
            return "one"
        return value if value in self.sessions else None

    def get(self, value):
        return SimpleNamespace(id=value) if value in self.sessions else None

    def list_all_sessions_with_info(self):
        return list(self.sessions.values())

    def get_or_create(self, value, _options=None):
        self.sessions.setdefault(
            value,
            {"id": value, "number": 3, "title": value, "turn_count": 0, "locked": False, "lock_pid": None},
        )
        return SimpleNamespace(id=value)

    def get_session_display_name(self, value):
        return f"#{self.sessions[value]['number']} {self.sessions[value]['title']}"

    def rename_session(self, value, title):
        if value not in self.sessions:
            return False
        self.sessions[value]["title"] = title
        return True

    def destroy(self, value, *, keep_files=True):
        del keep_files
        return self.destroy_result and self.sessions.pop(value, None) is not None


def test_resolve_and_list_variants(capsys) -> None:
    manager = _Manager()
    assert cli_commands._resolve_session(manager, "") is None
    assert cli_commands._resolve_session(manager, "1") == "one"
    assert cli_commands._resolve_session(manager, "2") == "two"
    assert cli_commands._resolve_session(manager, "missing") is None
    cli_commands.cmd_session_list(None, "one")
    cli_commands.cmd_session_list(manager, "one")
    cli_commands.cmd_session_list(manager, "one", markdown=True)
    output = capsys.readouterr().out
    assert "未初始化" in output and "← 当前" in output and "Two\\|Pipe" in output


@pytest.mark.asyncio
async def test_switch_success_missing_locked_and_lock_failure(monkeypatch, capsys) -> None:
    manager = _Manager()
    releases = []
    lock_results = {"one": (True, ""), "two": (True, "")}

    async def try_lock(session_id):
        return lock_results.get(session_id, (False, "busy"))

    result = await cli_commands.cmd_session_switch(
        manager, "one", "missing", try_lock, releases.append, lambda _sid: None
    )
    assert result == "one"

    result = await cli_commands.cmd_session_switch(
        manager, "one", "two", try_lock, releases.append, lambda _sid: 999
    )
    assert result == "one"

    manager.sessions["two"]["locked"] = False
    lock_results["two"] = (False, "busy")
    result = await cli_commands.cmd_session_switch(
        manager, "one", "two", try_lock, releases.append, lambda _sid: None
    )
    assert result == "one"

    lock_results["two"] = (True, "")
    monkeypatch.setattr(cli_commands, "_save_cli_session_state_on_switch", lambda *_: None)
    result = await cli_commands.cmd_session_switch(
        manager, "one", "two", try_lock, releases.append, lambda _sid: None
    )
    assert result == "two"
    output = capsys.readouterr().out
    assert "不存在" in output and "被其他实例占用" in output and "无法切换" in output and "已切换" in output


@pytest.mark.asyncio
async def test_create_rename_delete_outcomes(capsys) -> None:
    manager = _Manager()

    async def lock_ok(_sid):
        return True, ""

    async def lock_fail(_sid):
        return False, "busy"

    await cli_commands.cmd_session_create(None, "new", None, lock_ok)
    await cli_commands.cmd_session_create(manager, "new", "New", lock_fail)
    await cli_commands.cmd_session_create(manager, "new2", "New2", lock_ok)
    cli_commands.cmd_session_rename(None, "one", "x")
    cli_commands.cmd_session_rename(manager, "missing", "x")
    cli_commands.cmd_session_rename(manager, "one", "Renamed")
    cli_commands.cmd_session_delete(None, "one", "two", lambda _sid: None)
    cli_commands.cmd_session_delete(manager, "one", "missing", lambda _sid: None)
    cli_commands.cmd_session_delete(manager, "one", "one", lambda _sid: None)
    cli_commands.cmd_session_delete(manager, "one", "two", lambda _sid: None, keep_files=False)
    output = capsys.readouterr().out
    assert "加锁失败" in output and "已创建会话" in output and "已重命名" in output
    assert "不能删除" in output and "清除文件" in output

