"""类型边界修复对应的运行时行为回归测试。"""

from __future__ import annotations

import importlib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.core.plan_utils import _parse_depends_on, _resolve_step_depends_on
from miniagent.core.planner import _dict_to_plan
from miniagent.core.self_opt.proposal_engine import _pain_point_to_proposal
from miniagent.core.self_opt.types import PainPoint
from miniagent.engine.cli_commands import cmd_schedule
from miniagent.engine.cli_shell import _shell_argv
from miniagent.engine.cli_transcript import TranscriptBuffer
from miniagent.feishu.drive_extra import SearchApiError, search_docs
from miniagent.feishu.feishu_dedup import FeishuDeduplicator
from miniagent.feishu.types import FeishuConfig
from miniagent.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator
from miniagent.infrastructure.trace_stats import _TraceStatsAccumulator
from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec
from miniagent.tools.path_utils import resolve_path_simple
from miniagent.tools.schedule_tools import _manage_scheduled_task_handler


def test_dependency_parsers_reject_arbitrary_objects() -> None:
    marker = object()
    assert _resolve_step_depends_on(marker, {}) is None
    assert _parse_depends_on(marker) is None


def test_low_severity_pain_point_produces_low_risk_proposal(tmp_path: Path) -> None:
    proposal = _pain_point_to_proposal(
        PainPoint(
            category="testing",
            description="small issue",
            severity=1,
            frequency=1,
            suggestion="fix it",
        ),
        root=str(tmp_path),
    )
    assert proposal is not None
    assert proposal.risk_level == "low"


def test_posix_shell_argv_uses_explicit_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.engine.cli_shell as cli_shell

    monkeypatch.setattr(cli_shell.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/test-shell")
    assert _shell_argv("printf ok") == ["/bin/test-shell", "-c", "printf ok"]


def test_transcript_buffer_supports_index_reads() -> None:
    buffer = TranscriptBuffer(100, min_fragments=0)
    buffer.append(("class:test", "value"))
    assert buffer[0] == ("class:test", "value")


def test_search_docs_preserves_numeric_api_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.feishu.drive_client as drive_client

    monkeypatch.setattr(
        drive_client,
        "_http_request",
        lambda *_args, **_kwargs: {"code": "123", "msg": "denied"},
    )
    with pytest.raises(SearchApiError) as caught:
        search_docs(FeishuConfig("id", "secret"), "query", user_token="token")
    assert caught.value.code == 123


def test_deduplicator_evicts_oldest_completed_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.feishu.feishu_dedup as dedup_module

    monkeypatch.setattr(dedup_module, "DEDUP_MAX_SIZE", 2)
    dedup = FeishuDeduplicator(str(tmp_path))
    monkeypatch.setattr(dedup, "_maybe_schedule_flush", lambda: None)
    for message_id in ("one", "two", "three"):
        assert dedup.try_begin_processing(message_id)
        dedup.release_processing(message_id)
    assert len(dedup._processed) <= 2
    assert "mini-agent:one" not in dedup._processed


def test_buffered_ansi_fragment_flushes_after_live_turn() -> None:
    ansi_objects: list[object] = []
    coordinator = CliTranscriptCoordinator(
        lambda _style, _text: None,
        ansi_objects.append,
        parallel_sessions=True,
    )
    coordinator.begin_turn("live")
    coordinator.begin_turn("buffered")
    marker = object()
    coordinator.append_ansi("buffered", marker)
    coordinator.end_turn("buffered")
    assert ansi_objects == []
    coordinator.end_turn("live")
    assert ansi_objects == [marker]


def test_trace_memory_chars_accepts_numeric_metrics() -> None:
    stats = _TraceStatsAccumulator()
    stats._add_memory_read({"duration_ms": 1.5, "chars_loaded": 12.0})
    assert stats.memory_total_chars == 12


def test_resolve_path_simple_accepts_sequence_allowlist(tmp_path: Path) -> None:
    resolved = resolve_path_simple(str(tmp_path / "file.txt"), allowed=(str(tmp_path),))
    assert resolved == str(tmp_path / "file.txt")


@pytest.mark.asyncio
async def test_schedule_tool_lists_cron_expression(monkeypatch: pytest.MonkeyPatch) -> None:
    task = ScheduledTask(
        id="cron-task",
        name="Cron",
        prompt="run",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 8 * * *"),
    )
    import miniagent.scheduled_tasks.store as store

    monkeypatch.setattr(store, "load_tasks", lambda: [task])
    result = await _manage_scheduled_task_handler(
        {"action": "list"},
        SimpleNamespace(cli_dispatch_allow_mutations=True),
    )
    assert result.success
    assert 'cron "0 8 * * *"' in result.content


def test_invalid_step_thinking_level_falls_back() -> None:
    plan = _dict_to_plan(
        {
            "defaultStepThinkingLevel": "low",
            "steps": [{"description": "step", "thinkingLevel": "invalid"}],
        }
    )
    assert plan.steps[0].thinking_level == "low"


def test_parse_activity_log_with_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.core.self_opt.runtime_analyzer as analyzer

    monkeypatch.setattr(analyzer, "get_activity_log_dir", lambda: tmp_path)
    (tmp_path / "2026-07-12.md").write_text("## session-1\n", encoding="utf-8")
    result = analyzer.parse_activity_log("2026-07-12")
    assert result["sessions"] == ["session-1"]


@pytest.mark.asyncio
async def test_tui_force_fallback_after_optional_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.engine.cli_tui as tui

    fallback = AsyncMock()
    monkeypatch.setattr(tui, "run_cli_loop_fallback", fallback)
    monkeypatch.setattr(tui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(tui.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(tui, "get_config", lambda key, default=None: key == "cli.force_fallback")
    ctx = SimpleNamespace(
        engine=None,
        registry=None,
        monitor=None,
        channel_router=None,
        message_queue=None,
        outbound_channels=SimpleNamespace(),
        cli_outbound_dispatcher=None,
    )
    await tui.run_cli_loop(ctx, {}, [], [])
    fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unix_process_group_termination(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.infrastructure.process as process_module

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
async def test_feishu_im_handlers_cover_config_and_filter_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.feishu import upload_io

    im_tools = importlib.import_module("miniagent.tools.feishu_im_tools")
    cfg = FeishuConfig("id", "secret")
    monkeypatch.setattr(im_tools, "check_feishu_config_and_lark_oapi", lambda: (cfg, None))
    monkeypatch.setattr(im_tools, "default_receive_id_for_send", lambda *_args: ("chat", None))
    monkeypatch.setattr(im_tools, "effective_receive_id_type", lambda *_args: "chat_id")
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(upload_io, "upload_im_image", lambda *_args: "img-key")
    monkeypatch.setattr(upload_io, "send_im_image_message", lambda *_args, **_kwargs: (True, None))
    ctx = SimpleNamespace(cwd=str(tmp_path))
    sent = await im_tools._feishu_send_workspace_file(
        {"relative_path": "image.png", "as_image": True}, ctx
    )
    assert sent.success

    monkeypatch.setattr(upload_io, "delete_im_message", lambda *_args: (True, None))
    recalled = await im_tools._feishu_recall_message({"message_id": "m1"}, ctx)
    assert recalled.success

    async def resolved(*_args, **_kwargs):
        return "folder", None

    monkeypatch.setattr(im_tools, "resolve_parent_folder_token_async", resolved)
    import miniagent.feishu.drive_client as drive_client

    monkeypatch.setattr(
        drive_client,
        "list_folder_files_page",
        lambda *_args, **_kwargs: (
            [
                {"name": "skip", "token": "f1", "type": "file"},
                {"name": "Keep|Folder", "token": "t|2", "type": "folder"},
            ],
            None,
            False,
        ),
    )
    listed = await im_tools._feishu_list_drive_files(
        {"folders_only": True, "name_contains": "keep"}, ctx
    )
    assert listed.success
    assert "Keep\\|Folder" in listed.content

    monkeypatch.setattr(
        drive_client,
        "list_folder_files_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("API down")),
    )
    failed = await im_tools._feishu_list_drive_files({}, ctx)
    assert not failed.success
    assert "API down" in failed.content


def test_feishu_doc_table_values_are_validated_and_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.feishu.docx.tables as tables
    from miniagent.tools.feishu_doc_tools import _action_write_table_cells

    written: list[list[list[str]]] = []
    monkeypatch.setattr(
        tables,
        "write_table_cells",
        lambda _cfg, _doc, _table, values: written.append(values),
    )
    cfg = FeishuConfig("id", "secret")
    invalid = _action_write_table_cells(
        {"doc_token": "doc", "table_block_id": "table", "values": "{}"}, cfg
    )
    assert not invalid.success
    valid = _action_write_table_cells(
        {"doc_token": "doc", "table_block_id": "table", "values": [[1, "x"]]}, cfg
    )
    assert valid.success
    assert written == [[['1', 'x']]]


def test_cli_schedule_list_and_update_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.scheduled_tasks.store as store

    task = ScheduledTask(
        id="job",
        name="Job",
        prompt="old",
        schedule=ScheduleSpec(kind="cron", cron_expr="0 8 * * *"),
    )
    monkeypatch.setattr(store, "load_tasks", lambda: [task])
    monkeypatch.setattr(store, "save_tasks", lambda _tasks: None)
    monkeypatch.setattr(store, "compute_initial_next_run", lambda *_args: 1.0)
    assert 'cron "0 8 * * *"' in cmd_schedule("/schedule list", allow_mutations=True)
    once = cmd_schedule(
        "/schedule update job once 2030-01-01T00:00:00 primary -- new once",
        allow_mutations=True,
    )
    assert "已更新" in once
    cron = cmd_schedule(
        '/schedule update job cron "0 9 * * *" primary -- new cron',
        allow_mutations=True,
    )
    assert "已更新" in cron


@pytest.mark.asyncio
async def test_command_dispatch_instance_and_query() -> None:
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.infrastructure.message_queue import MessageQueueManager

    queue = MessageQueueManager()
    runtime = SimpleNamespace(
        message_queue=queue,
        channel_router=SimpleNamespace(),
        feishu=SimpleNamespace(),
    )
    state = {"runtime_ctx": runtime, "instance_id": -1}
    instance_output = await dispatch_command("/instance list", state=state, capture=True)
    query_output = await dispatch_command("/query", state=state, capture=True)
    assert isinstance(instance_output, str)
    assert isinstance(query_output, str)


def test_feishu_ws_client_initializes_owned_task_state() -> None:
    from miniagent.feishu.ws_client import FeishuWsClient

    client = FeishuWsClient(
        app_id="id", app_secret="secret", event_handler=lambda _event: None
    )
    assert client.receive_task is None


@pytest.mark.asyncio
async def test_uninstall_skill_hot_refreshes_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.skills.refresh as refresh_module
    skill_tools = importlib.import_module("miniagent.tools.skills")

    (tmp_path / "demo").mkdir()
    monkeypatch.setattr(skill_tools, "_get_skills_root", lambda: str(tmp_path))

    async def refreshed(*_args, **_kwargs):
        return SimpleNamespace(removed_tools=["tool"])

    monkeypatch.setattr(refresh_module, "refresh_skills", refreshed)
    runtime = SimpleNamespace(registry=object(), skill_registry=object())
    ctx = SimpleNamespace(
        cli_loop_state={"runtime_ctx": runtime, "session_manager": None}
    )
    result = await skill_tools._uninstall_handler({"slug": "demo"}, ctx)
    assert result.success
    assert "已从当前 Agent 中移除" in result.content


@pytest.mark.asyncio
async def test_unix_process_group_escalates_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.infrastructure.process as process_module

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


async def _completed_coroutine() -> int:
    """返回已完成协程，供进程超时测试替身使用。"""
    return 0


@pytest.mark.asyncio
async def test_unix_process_group_logs_failed_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.infrastructure.process as process_module

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
    import miniagent.infrastructure.process as process_module

    def missing(_pid):
        raise ProcessLookupError("gone")

    monkeypatch.setattr(process_module.os, "getpgid", missing, raising=False)
    monkeypatch.setattr(process_module.os, "killpg", lambda *_args: None, raising=False)
    await process_module._kill_unix(
        SimpleNamespace(pid=10, wait=lambda: _completed_coroutine())
    )


def test_scheduled_task_save_preserves_primary_error_when_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import miniagent.scheduled_tasks.store as store

    monkeypatch.setattr(store, "tasks_file_path", lambda: str(tmp_path / "tasks.json"))
    monkeypatch.setattr(store, "tasks_json_lock", lambda: nullcontext())
    monkeypatch.setattr(
        store,
        "atomic_dump_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace")),
    )
    with pytest.raises(OSError, match="replace"):
        store.save_tasks([])
