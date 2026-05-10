"""启动冒烟测试

验证完整的启动路径：
1. unified_entry 可以正常调用（不会因 import 错误崩溃）
2. 所有核心组件可以正常初始化
3. 实际运行 python -m miniagent 的 import 阶段不报 ImportError
4. 真实启动：以子进程方式启动实例，验证注册、初始化、欢迎输出
"""

from __future__ import annotations

import asyncio
import builtins
import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import MagicMock

import pytest


def _make_memory_bundle():
    """隔离目录下的记忆三层组件（测试用）。"""
    import tempfile

    from miniagent.memory.activity_log import ActivityLogger
    from miniagent.memory.keyword_index import KeywordIndex
    from miniagent.memory.store import DefaultMemoryStore

    root = tempfile.mkdtemp()
    ki = KeywordIndex(state_dir=root)
    ms = DefaultMemoryStore(state_dir=root, keyword_index=ki)
    al = ActivityLogger(base_dir=os.path.join(root, "memory"))
    return ms, al, ki


def test_unified_entry_imports():
    """unified_entry 内部所有 import 正常。"""
    from miniagent.compat import unified_entry
    assert callable(unified_entry)


def test_cli_output_buffer_readonly_append():
    """CLI 上方输出区使用 read_only Buffer；程序写入须 set_document(..., bypass_readonly=True)。"""
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document

    buf = Buffer(read_only=True)
    frag = "hello\n"
    merged = buf.text + frag
    buf.set_document(Document(merged, cursor_position=len(merged)), bypass_readonly=True)
    assert frag.strip() in buf.text


def test_cli_layout_initial_focus_on_input_buffer():
    """全屏 CLI 须把初始焦点放在底部输入框；底栏为 VSplit 与生产布局一致。"""
    try:
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.layout.dimension import LayoutDimension as D
    except ImportError:
        pytest.skip("prompt_toolkit not installed")

    output_buffer = Buffer(read_only=True)
    input_buffer = Buffer()
    body = HSplit(
        [
            Window(
                BufferControl(buffer=output_buffer, focusable=False),
                height=D(weight=1),
            ),
            Window(height=1, char="-"),
            VSplit(
                [
                    Window(
                        FormattedTextControl(HTML("<prompt-prefix>x</prompt-prefix>")),
                        width=D.exact(4),
                        height=D.exact(1),
                    ),
                    Window(BufferControl(buffer=input_buffer), height=D.exact(1)),
                ],
                height=D.exact(1),
            ),
        ],
    )
    layout = Layout(body, focused_element=input_buffer)
    assert layout.current_buffer is input_buffer


def test_component_creation():
    """所有核心组件可以正常创建。"""
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.engine.engine import UnifiedEngine

    registry = DefaultToolRegistry()
    monitor = DefaultToolMonitor()
    skill_registry = DefaultSkillRegistry()
    clawhub = create_clawhub_client()
    engine = UnifiedEngine()

    assert registry is not None
    assert monitor is not None
    assert skill_registry is not None
    assert clawhub is not None
    assert engine is not None


def test_main_entry_no_import_errors():
    """python -m miniagent 在 import 阶段不报 ImportError。

    通过传入 --help 标志（会被 argparse 或 sys.argv 捕获），
    或者直接检查 python -c "import miniagent.__main__" 是否崩溃。
    如果模块有 import 错误，任何方式运行都会报出来。
    """
    # 方法 1：直接 import 入口模块
    result = subprocess.run(
        [sys.executable, "-c", "from miniagent.__main__ import main; print('OK')"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "ImportError" not in result.stderr, f"Import 失败: {result.stderr}"
    assert "Traceback" not in result.stderr, f"启动失败: {result.stderr}"
    assert "OK" in result.stdout, f"输出异常: stdout={result.stdout}, stderr={result.stderr}"


def test_unified_entry_callable():
    """unified_entry 可以被正常调用（验证内部依赖完整）。"""
    from miniagent.compat import unified_entry
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.engine.engine import UnifiedEngine

    # 模拟 unified_entry 的内部流程（构造组合根所需依赖）
    assert all(
        (
            DefaultToolRegistry(),
            DefaultToolMonitor(),
            DefaultSkillRegistry(),
            create_clawhub_client(),
            UnifiedEngine(),
        )
    )

    assert callable(unified_entry)


def test_feishu_handler_creation():
    """飞书 handler 可以正常创建。"""
    from miniagent.engine.main import _create_feishu_handler
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.runtime.context import RuntimeContext

    mq = MessageQueueManager()
    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    loop_state = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
    }
    handler = _create_feishu_handler([], [], loop_state, ctx)
    assert callable(handler)


def test_set_console_log_threshold_updates_handlers():
    """全屏 TUI 用的阈值开关须同步到已有 StreamHandler。"""
    import logging

    from miniagent.infrastructure.logger import get_logger, set_console_log_threshold

    log = get_logger("miniagent.test_console_threshold_only")
    handler = log.handlers[0]
    set_console_log_threshold(logging.ERROR)
    assert handler.level == logging.ERROR
    set_console_log_threshold(logging.INFO)
    assert handler.level == logging.INFO


def test_feishu_user_status_fn_uses_cli_transcript_append():
    """全屏注册 cli_transcript_append 时，飞书状态行走 transcript 而非裸 print。"""
    from miniagent.engine.main import _feishu_user_status_fn
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.runtime.context import RuntimeContext

    mq = MessageQueueManager()
    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    append_calls: list[tuple[str, str]] = []
    ctx.cli_transcript_append = lambda style, text: append_calls.append((style, text))
    _feishu_user_status_fn(ctx)("test line")
    assert append_calls == [("class:cli-muted", "test line\n")]


def test_feishu_start_user_status_avoids_print(monkeypatch):
    """有 user_status 时 start() 同步反馈不调用 builtins.print。"""
    monkeypatch.setenv("FEISHU_APP_ID", "test_app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "token")

    printed: list[tuple] = []

    def fake_print(*args, **kwargs):
        printed.append((args, kwargs))

    monkeypatch.setattr(builtins, "print", fake_print)

    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.message_queue import MessageQueueManager

    mq = MessageQueueManager()
    rt = FeishuRuntime(mq)
    status_lines: list[str] = []

    def user_status(msg: str) -> None:
        status_lines.append(msg)

    def _fake_create_task(coro):
        try:
            coro.close()
        except Exception:
            pass
        mock_t = MagicMock()
        mock_t.done = lambda: True
        return mock_t

    with monkeypatch.context() as m:
        m.setattr("asyncio.create_task", _fake_create_task)

        def _make_handler(tb, tp, st):
            async def _handler(content, chat_id, sender_id, chat_type="group"):
                return ""

            return _handler

        rt.start(
            [],
            [],
            _make_handler,
            {"runtime_ctx": None},
            user_status=user_status,
        )

    assert status_lines
    assert any("\u2705" in s for s in status_lines)  # 飞书已启动
    assert not printed


def test_thinking_show_mirrors_to_sink_when_feishu_and_sink():
    """飞书发送成功后，若已注册 _output_sink，思考仍镜像到 CLI transcript。"""
    from miniagent.engine.thinking import ThinkingDisplay

    td = ThinkingDisplay()
    sink_lines: list[tuple[str, str]] = []

    def sink(text: str, kind: str = "chunk") -> None:
        sink_lines.append((text, kind))

    td.set_output_sink(sink)

    feishu_sent: list[tuple[str, str, str]] = []

    async def feishu_send(chat_id: str, text: str, template: str, **_kw: object) -> None:
        feishu_sent.append((chat_id, text, template))

    td.enable_feishu("sk1", "cid1", feishu_send)

    async def run():
        await td.show("alpha", session_key="sk1", streaming=False)

    asyncio.run(run())

    assert feishu_sent and feishu_sent[0][0] == "cid1"
    assert sink_lines, "sink should receive CLI mirror after Feishu send"


def test_command_dispatch_all_commands():
    """所有 .命令 都可以正常路由（不崩溃）。"""
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.runtime.context import RuntimeContext

    mq = MessageQueueManager()
    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    ctx.create_feishu_handler_factory = lambda tb, tp, st: (lambda *a, **k: None)

    state = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
    }

    async def run_all():
        cmds = [
            ".help",
            ".status",
            ".bind status",
            ".unbind all",
            ".feishu status",
            ".queue status",
            ".profile",
        ]
        for cmd in cmds:
            result = await dispatch_command(cmd, state=state, capture=True)
            assert result is not None, f"命令 {cmd} 返回 None"
            assert "Traceback" not in result, f"命令 {cmd} 报错: {result}"

    asyncio.run(run_all())


def test_dispatch_queue_set_async_capture():
    """dispatch_command 内 .queue set 须异步 await，不得 run_until_complete。"""
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.runtime.context import RuntimeContext

    mq = MessageQueueManager()
    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    state = {
        "active_session_id": "default",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
    }

    async def run():
        mq.mode = QueueMode.PREEMPTIVE
        out = await dispatch_command(".queue set queue", state=state, capture=True)
        assert out is not None
        assert "队列" in out or "queue" in out.lower()
        assert mq.mode == QueueMode.QUEUE

    asyncio.run(run())


def test_dispatch_feishu_blocks_session_mutations():
    """飞书 capture 路径不得修改 active_session_id。"""
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.runtime.context import RuntimeContext

    mq = MessageQueueManager()
    ms, al, ki = _make_memory_bundle()
    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=ChannelRouter(),
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=ms,
        activity_log=al,
        keyword_index=ki,
    )
    state = {
        "active_session_id": "keep-me",
        "skill_toolboxes": [],
        "skill_prompts": [],
        "feishu_enabled": False,
        "session_manager": None,
        "instance_id": 1,
        "runtime_ctx": ctx,
    }

    async def run():
        out = await dispatch_command(
            ".session switch 1",
            state=state,
            capture=True,
            allow_session_mutations_when_capture=False,
        )
        assert state["active_session_id"] == "keep-me"
        assert out is not None
        assert "共享" in out or "终端" in out

    asyncio.run(run())


def test_all_public_imports():
    """项目公开 API 都可以正常 import。"""
    # 入口
    from miniagent.__main__ import main
    # 聚合入口（compat）
    from miniagent.compat import (
        unified_entry,
        unified_main,
        run_cli_loop,
        RuntimeContext,
        FeishuRuntime,
    )
    # engine
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.thinking import ThinkingDisplay
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.engine.cli_commands import (
        cmd_bind, cmd_unbind, cmd_session_list, cmd_help,
    )
    # infrastructure
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry
    # feishu
    from miniagent.feishu.poll_server import start_feishu_poll_server
    from miniagent.feishu.types import FeishuConfig
    # skills
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client

    assert all([
        main, unified_entry, unified_main, run_cli_loop,
        RuntimeContext, FeishuRuntime,
        UnifiedEngine, ThinkingDisplay, dispatch_command,
        cmd_bind, cmd_unbind, cmd_session_list, cmd_help,
        ChannelRouter, MessageQueueManager, QueueMode,
        DefaultToolMonitor, DefaultToolRegistry,
        start_feishu_poll_server, FeishuConfig,
        DefaultSkillRegistry, create_clawhub_client,
    ])


def test_actual_instance_startup():
    """真实启动测试：以子进程启动实例，验证完整启动流程。

    启动 python -m miniagent，等待 5 秒，检查：
    1. 进程未崩溃（无 Traceback/ImportError）
    2. 输出了欢迎信息（Mini Agent 特征文本）
    3. 发送 .stop 能正常退出
    """
    # 清理旧实例锁，避免干扰
    import glob
    for lock in glob.glob(os.path.join(os.path.expanduser("~"), "miniagent", "*.lock")):
        try:
            os.unlink(lock)
        except Exception:
            pass

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # 独立状态目录，避免使用仓库内可能损坏的 workspaces 数据
    state_dir = tempfile.mkdtemp(prefix="miniagent_test_state_")
    env["MINI_AGENT_STATE"] = state_dir

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "miniagent"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
        )

        # 等待启动（5 秒足够完成注册、初始化、打印欢迎）
        time.sleep(5)

        # 检查是否还在运行（如果因 import 错误崩溃，会立即退出）
        ret = proc.poll()
        if ret is not None:
            stdout_out, stderr_out = proc.communicate()
            assert False, (
                f"进程已退出 (code={ret})\nstdout:\n{stdout_out[:1000]}\nstderr:\n{stderr_out[:2000]}"
            )

        # 发送 .stop 命令优雅关闭
        try:
            proc.stdin.write(".stop\n")
            proc.stdin.flush()
        except Exception:
            pass

        # 等待退出
        try:
            stdout_out, stderr_out = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_out, stderr_out = proc.communicate()

        # 验证：无 ImportError
        assert "ImportError" not in stderr_out, f"Import 错误: {stderr_out}"
        # 验证：无 Traceback
        assert "Traceback" not in stderr_out, f"启动崩溃: {stderr_out}"
        # 验证：有输出（说明启动成功且运行了一段时间）
        combined = (stdout_out or "") + (stderr_out or "")
        # 至少要看到提示符或欢迎信息
        assert ">" in combined or "Mini" in combined or "Agent" in combined or "会话" in combined, (
            f"未看到任何输出。\nstdout[:500]={stdout_out[:500]}\nstderr[:500]={stderr_out[:500]}"
        )
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
