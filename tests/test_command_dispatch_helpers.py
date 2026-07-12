"""命令分派审查、改进、状态与自测辅助函数契约。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.engine import command_dispatch as dispatch


def test_last_qa_reads_versioned_history_and_handles_failures(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    history = {
        "schema_version": 1,
        "messages": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ],
    }
    (tmp_path / "history.json").write_text(json.dumps(history), encoding="utf-8")
    session = SimpleNamespace(conversation_history=[], workspace_path=str(files))
    manager = SimpleNamespace(get=lambda _sid: session)
    assert dispatch._get_last_qa(manager, "s") == ("question", "answer")
    assert dispatch._get_last_qa(SimpleNamespace(get=lambda _sid: None), "s") == (None, None)
    session.conversation_history = [{"role": "user", "content": "only"}]
    assert dispatch._get_last_qa(manager, "s") == (None, None)


def test_last_qa_loader_and_compatibility_file_failures(tmp_path: Path) -> None:
    session = SimpleNamespace(conversation_history=[], workspace_path=str(tmp_path / "files"))
    manager = SimpleNamespace(
        get=lambda _sid: session,
        load_session_history=lambda _sid: [
            {"role": "user", "content": "loaded question"},
            {"role": "assistant", "content": "loaded answer"},
        ],
    )
    assert dispatch._get_last_qa(manager, "s") == ("loaded question", "loaded answer")

    manager.load_session_history = MagicMock(side_effect=RuntimeError("broken"))
    tmp_path.joinpath("history.json").write_text("not-json", encoding="utf-8")
    assert dispatch._get_last_qa(manager, "s") == (None, None)

    tmp_path.joinpath("history.json").write_text(
        json.dumps(
            [
                {"role": "user", "content": "legacy question"},
                {"role": "tool", "content": "tool"},
                {"role": "assistant", "content": "legacy answer"},
            ]
        ),
        encoding="utf-8",
    )
    assert dispatch._get_last_qa(manager, "s") == ("legacy question", "legacy answer")


@pytest.mark.asyncio
async def test_review_empty_clean_and_iterative(monkeypatch) -> None:
    import miniagent.core.llm_json as llm_module

    responses = iter(
        [
            {},
            {"has_issues": False, "issues": []},
            {
                "has_issues": True,
                "issues": [{"description": "缺少细节"}],
                "improved_answer": "better",
            },
            {"has_issues": False, "issues": []},
        ]
    )

    async def fake_llm_json(**_kwargs):
        return next(responses)

    monkeypatch.setattr(llm_module, "llm_json", fake_llm_json)
    assert await dispatch._run_review("q", "a", capture=True) is None
    assert await dispatch._run_review("q", "a", capture=True) is None
    result = await dispatch._run_review("q", "a", extra_feedback="more", capture=True)
    assert result and "better" in result


@pytest.mark.asyncio
async def test_review_output_fallbacks_and_iteration_limits(monkeypatch) -> None:
    import miniagent.core.llm_json as llm_module

    writes: list[tuple[str, str]] = []
    responses = iter(
        [
            {
                "has_issues": True,
                "issues": [{"description": "issue"}],
                "improved_answer": "first",
            },
            {},
            {
                "has_issues": True,
                "issues": [{"description": "issue"}],
                "improved_answer": "second",
            },
        ]
    )

    async def fake_llm_json(**_kwargs):
        return next(responses)

    monkeypatch.setattr(llm_module, "llm_json", fake_llm_json)
    result = await dispatch._run_review(
        "q",
        "a",
        capture=True,
        max_iterations=2,
        term_write=lambda style, text: writes.append((style, text)),
    )
    assert result is not None and result.endswith("first")
    assert writes

    def broken_write(*_args):
        raise RuntimeError("terminal closed")

    assert await dispatch._run_review(
        "q", "a", capture=True, max_iterations=1, term_write=broken_write
    )


@pytest.mark.asyncio
async def test_improve_success_failure_and_term_sink(monkeypatch) -> None:
    import miniagent.core.llm_json as llm_module

    calls = []

    async def success(**_kwargs):
        return {"improved_answer": "new answer"}

    monkeypatch.setattr(llm_module, "llm_json", success)
    result = await dispatch._run_improve(
        "q", "a", ["clear"], capture=True, term_write=lambda *args: calls.append(args)
    )
    assert result and "new answer" in result and calls

    async def empty(**_kwargs):
        return {}

    monkeypatch.setattr(llm_module, "llm_json", empty)
    assert await dispatch._run_improve("q", "a", [], capture=True) is None


def test_capture_and_status_busy_bindings() -> None:
    assert "boom" in dispatch._capture(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    queue = SimpleNamespace(
        get_status=lambda: {
            "mode": "queue",
            "chats": {
                "cli": {"busy": True, "pending": 2, "elapsed": 3.2},
                "other": {"busy": False, "pending": 0, "elapsed": None},
            },
        }
    )
    router = SimpleNamespace(
        CLI_CHANNEL="cli",
        get_all_bindings=lambda: {"feishu:chat": "session"},
        get_primary=lambda: None,
        resolve=lambda _channel: "session",
    )
    runtime = SimpleNamespace(
        message_queue=queue,
        channel_router=router,
        feishu=SimpleNamespace(is_running=lambda: True),
    )
    manager = SimpleNamespace(get_session_display_name=lambda _sid: "#1 会话")
    result = dispatch._format_status(
        {"runtime_ctx": runtime, "instance_id": 2, "active_session_id": "s", "session_manager": manager}
    )
    assert "运行中" in result and "等待: 2" in result and "通道绑定" in result


@pytest.mark.asyncio
async def test_run_test_capture_and_real_mode_validation(monkeypatch) -> None:
    import miniagent.testing.test_runner as runner_module

    report = SimpleNamespace(
        passed=1,
        total=2,
        pass_rate=0.5,
        failed=1,
        skipped=0,
        duration_seconds=0.1,
        results=[SimpleNamespace(passed=False, error_message="failed", sample_name="case")],
    )

    async def fake_run(**_kwargs):
        return report

    monkeypatch.setattr(runner_module, "run_self_test", fake_run)
    output = await dispatch._run_test(capture=True)
    assert "1/2" in output and "case" in output
    assert "需要 registry" in await dispatch._run_test(mock=False, capture=True)


def test_list_samples_and_last_status(monkeypatch) -> None:
    import miniagent.testing.test_runner as runner_module

    sample = SimpleNamespace(category="core", name="sample", description="desc", input="q", priority=1)
    fake = SimpleNamespace(
        load_samples=lambda: [sample],
        get_last_report=lambda: {
            "timestamp": "now",
            "total": 2,
            "passed": 1,
            "failed": 1,
            "skipped": 0,
            "duration_seconds": 0.2,
        },
    )
    monkeypatch.setattr(runner_module, "TestRunner", lambda: fake)
    assert "sample" in dispatch._list_test_samples()
    assert "50.0%" in dispatch._get_test_status()
    fake.load_samples = lambda: []
    fake.get_last_report = lambda: None
    assert "暂无" in dispatch._list_test_samples()
    assert "暂无" in dispatch._get_test_status()
