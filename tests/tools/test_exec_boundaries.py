"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.engine.cli_shell import _shell_argv
from miniagent.assistant.tools.path_utils import resolve_path_simple


async def _completed_coroutine() -> int:
    """返回已完成协程，供进程超时测试替身使用。"""
    return 0

def test_posix_shell_argv_uses_explicit_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.engine.cli_shell as cli_shell

    monkeypatch.setattr(cli_shell.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/test-shell")
    assert _shell_argv("printf ok") == ["/bin/test-shell", "-c", "printf ok"]

def test_resolve_path_simple_accepts_sequence_allowlist(tmp_path: Path) -> None:
    resolved = resolve_path_simple(str(tmp_path / "file.txt"), allowed=(str(tmp_path),))
    assert resolved == str(tmp_path / "file.txt")

@pytest.mark.asyncio
async def test_unix_process_group_termination(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.infrastructure.process as process_module

    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(process_module.os, "getpgid", lambda pid: pid + 1, raising=False)
    monkeypatch.setattr(
        process_module.os,
        "killpg",
        lambda pgid, signal: signals.append((pgid, signal)),
        raising=False,
    )
    proc = SimpleNamespace(pid=10, wait=AsyncMock(return_value=0))
    await process_module._kill_unix(proc)
    assert signals == [(11, 15)]

@pytest.mark.asyncio
async def test_unix_process_group_escalates_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.infrastructure.process as process_module

    signals: list[int] = []
    monkeypatch.setattr(process_module.os, "getpgid", lambda _pid: 1, raising=False)
    monkeypatch.setattr(
        process_module.os,
        "killpg",
        lambda _pgid, signal: signals.append(signal),
        raising=False,
    )
    calls = 0

    async def fake_wait_for(awaitable, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        awaitable.close()
        if calls == 1:
            raise TimeoutError
        return 0

    monkeypatch.setattr(process_module.asyncio, "wait_for", fake_wait_for)
    proc = SimpleNamespace(pid=10, wait=lambda: _completed_coroutine())
    await process_module._kill_unix(proc)
    assert signals == [15, 9]

@pytest.mark.asyncio
async def test_unix_process_group_logs_failed_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.infrastructure.process as process_module

    monkeypatch.setattr(process_module.os, "getpgid", lambda _pid: 1, raising=False)
    monkeypatch.setattr(process_module.os, "killpg", lambda *_args: None, raising=False)
    calls = 0

    async def fake_wait_for(awaitable, *, timeout):
        nonlocal calls
        del timeout
        calls += 1
        awaitable.close()
        if calls == 1:
            raise TimeoutError
        raise OSError("kill wait failed")

    monkeypatch.setattr(process_module.asyncio, "wait_for", fake_wait_for)
    proc = SimpleNamespace(pid=10, wait=lambda: _completed_coroutine())
    await process_module._kill_unix(proc)
    assert calls == 2

@pytest.mark.asyncio
async def test_unix_process_group_handles_missing_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.infrastructure.process as process_module

    def missing(_pid):
        raise ProcessLookupError("gone")

    monkeypatch.setattr(process_module.os, "getpgid", missing, raising=False)
    monkeypatch.setattr(process_module.os, "killpg", lambda *_args: None, raising=False)
    await process_module._kill_unix(
        SimpleNamespace(pid=10, wait=lambda: _completed_coroutine())
    )
