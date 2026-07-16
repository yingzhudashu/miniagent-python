"""全屏 TUI 组合根的无终端行为刻画测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent.assistant.application.messaging.channels import ChannelRegistry
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.infrastructure.channel_router import ChannelRouter
from miniagent.assistant.infrastructure.message_queue import MessageQueueManager


class _ThinkingDisplayStub:
    """记录 TUI 注册的输出槽和宽度回调。"""

    def __init__(self) -> None:
        self.output_sink = None
        self.width_callback = None

    def set_output_sink(self, sink) -> None:
        self.output_sink = sink

    def set_cli_markdown_width(self, callback) -> None:
        self.width_callback = callback


class _SessionManagerStub:
    """提供空历史，避免冒烟测试读取用户真实会话。"""

    def load_session_history_range(self, *_args, **_kwargs):
        return [], 0

    def get(self, _session_id):
        return None


@pytest.mark.asyncio
async def test_fullscreen_tui_builds_and_exits_without_user_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构建完整布局后以 ``__exit__`` 退出，不启动 Agent 或外部通道。"""
    import prompt_toolkit.application
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    import miniagent.assistant.engine.cli_tui as tui
    import miniagent.assistant.engine.session_continue as session_continue
    import miniagent.assistant.engine.session_lock as session_lock

    inputs = iter((None, "   ", "/copy", "__exit__"))

    async def exit_immediately(_application):
        return next(inputs)

    real_application = prompt_toolkit.application.Application
    monkeypatch.setattr(real_application, "run_async", exit_immediately)

    def application_factory(*args, **kwargs):
        """使用内存终端构造真实应用，避免 Windows 测试进程探测控制台。"""
        kwargs.setdefault("input", DummyInput())
        kwargs.setdefault("output", DummyOutput())
        return real_application(*args, **kwargs)

    # run_cli_loop 在函数体内导入 Application，因此替换模块属性即可覆盖该局部导入。
    monkeypatch.setattr(prompt_toolkit.application, "Application", application_factory)
    monkeypatch.setattr(tui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(tui.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(tui, "resolve_cli_history_file", lambda: str(tmp_path / "history.txt"))
    monkeypatch.setattr(session_lock, "release_session_lock", lambda _session_id: None)
    monkeypatch.setattr(session_continue, "save_cli_session_state", lambda *_args: None)
    monkeypatch.setattr(tui, "unregister_instance", lambda: None)
    monkeypatch.setattr(tui, "copy_text_to_system_clipboard", lambda _text: True)

    engine = SimpleNamespace(thinking=_ThinkingDisplayStub())
    router = ChannelRouter()
    queue = MessageQueueManager()
    ctx = SimpleNamespace(
        engine=engine,
        registry=SimpleNamespace(),
        monitor=SimpleNamespace(),
        channel_router=router,
        message_queue=queue,
        outbound_channels=ChannelRegistry(),
        background_tasks=SimpleNamespace(),
        cli_outbound_dispatcher=None,
        cli_transcript_append=None,
        cli_transcript_append_ansi=None,
        cli_transcript_coordinator=None,
        create_feishu_handler_factory=None,
    )
    state: CliLoopState = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": _SessionManagerStub(),  # type: ignore[typeddict-item]
        "instance_id": 1,
        "runtime_ctx": ctx,  # type: ignore[typeddict-item]
        "feishu_p2p_synced_senders": set(),
    }

    await tui.run_cli_loop(ctx, state, [], [])

    assert callable(engine.thinking.output_sink)
    assert callable(engine.thinking.width_callback)
    assert ctx.cli_transcript_append is None
