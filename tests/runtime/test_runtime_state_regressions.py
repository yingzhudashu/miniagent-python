"""Focused regressions migrated from test_diff_gate_new_modules.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.infrastructure import instance_render


def test_instance_render_empty_single_and_multi_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.paths.resolve_registry_state_dir", lambda: "C:/state"
    )
    monkeypatch.setattr(instance_render.os, "getpid", lambda: 10)
    assert "暂无运行实例" in instance_render.format_instances_markdown([])
    assert "暂无运行实例" in instance_render.format_instances_table([])

    items = [
        {
            "instance_id": 1, "pid": 10, "mode": "both", "project_dir": "C:/a/project",
            "project_key": "p1", "start_time": "2026-01-01T01:02:03Z",
            "active_sessions": ["s"], "hostname": "host|one", "state_dir": "C:/state",
        },
        {
            "instance_id": 2, "pid": 20, "mode": "cli", "cwd": "D:/b/project",
            "project_state_dir": "D:/state/projects/p2", "start_time": "?",
            "active_sessions": [], "hostname": "host\ntwo", "state_dir": "D:/other",
        },
    ]
    markdown = instance_render.format_instances_markdown(items)
    assert "projects/p1" in markdown and "projects/p2" in markdown
    assert "host\\|one" in markdown and "当前" in markdown and "状态目录" in markdown
    table = instance_render.format_instances_table(items)
    assert "canonical" in table and "← 当前" in table and "状态目录" in table

@pytest.mark.asyncio
async def test_runtime_service_start_updates_state_and_starts_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.assistant.bootstrap.runtime_services as runtime_services
    from miniagent.assistant.engine import init as init_module
    from miniagent.assistant.engine import main, parallel_config

    session_manager = object()
    monkeypatch.setattr(
        init_module,
        "init_subsystems",
        AsyncMock(return_value=(object(), ["toolbox"], ["prompt"], "session", session_manager)),
    )
    configure = MagicMock()
    monkeypatch.setattr(parallel_config, "configure_message_queue_for_parallel", configure)
    lifecycle = SimpleNamespace(start=AsyncMock())
    builder = MagicMock(return_value=lifecycle)
    monkeypatch.setattr(runtime_services, "build_runtime_lifecycle_manager", builder)
    engine = SimpleNamespace(set_active_session_key=MagicMock())
    ctx = SimpleNamespace(
        registry=object(), skill_registry=object(), channel_router=object(), clawhub=object(),
        memory=SimpleNamespace(keyword_index=object()), message_queue=object(), engine=engine,
        lifecycle_manager=None, cli_transcript_append=None,
    )
    state = {
        "active_session_id": "", "skill_toolboxes": [], "skill_prompts": [],
        "feishu_enabled": False, "session_manager": None, "instance_id": 1,
        "runtime_ctx": ctx, "feishu_p2p_synced_senders": set(),
    }
    result = await main._start_runtime_services(ctx, state)
    assert result == (["toolbox"], ["prompt"], "session")
    assert state["session_manager"] is session_manager and ctx.lifecycle_manager is lifecycle
    configure.assert_called_once_with(ctx.message_queue)
    engine.set_active_session_key.assert_called_once_with("session")
    lifecycle.start.assert_awaited_once()

def test_runtime_initial_state_conflict_and_windows_vt_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from miniagent.assistant.engine import main

    monkeypatch.setattr(main, "register_instance", lambda **_kwargs: {"instance_id": 7})
    state = main._initial_runtime_state(SimpleNamespace(), True)
    assert state["instance_id"] == 7 and state["feishu_enabled"] is True

    conflict = main.ProjectDirConflictError({"pid": 1, "project_dir": "x"})
    monkeypatch.setattr(main, "register_instance", MagicMock(side_effect=conflict))
    monkeypatch.setattr(main, "format_project_conflict_message", lambda _meta: "conflict")
    with pytest.raises(SystemExit) as exc:
        main._initial_runtime_state(SimpleNamespace(), False)
    assert exc.value.code == 2 and "conflict" in capsys.readouterr().out

    import ctypes

    class _BrokenWindll:
        @property
        def kernel32(self):
            raise RuntimeError("no console")

    monkeypatch.setattr(ctypes, "windll", _BrokenWindll(), raising=False)
    main._enable_windows_vt()
