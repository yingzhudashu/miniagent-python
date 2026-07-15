"""Line-oriented CLI used when the prompt_toolkit TUI is unavailable."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from miniagent.application.messaging import (
    InboundTurnCoordinator,
    OrderedOutboundDispatcher,
)
from miniagent.bootstrap.application import ApplicationContainer
from miniagent.contracts.messages import (
    ChannelTarget,
    InboundMessage,
    OutboundEvent,
    OutboundEventKind,
)
from miniagent.engine.cli_files import process_cli_file_markers
from miniagent.engine.cli_history import (
    prime_fallback_readline_history,
    resolve_cli_history_file,
)
from miniagent.engine.cli_inbound import (
    CLI_CHANNEL,
    CLI_CONVERSATION_ID,
    build_cli_inbound_message,
)
from miniagent.engine.cli_outbound import (
    CliChannelAdapter,
    build_cli_outbound_event,
    build_cli_thinking_event,
)
from miniagent.engine.cli_shell import run_cli_shell_command
from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.clipboard import copy_text_to_system_clipboard
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.engine.utils import feishu_user_status_fn, get_render_width
from miniagent.infrastructure.instance import heartbeat, unregister_instance
from miniagent.infrastructure.json_config import get_config
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX

_logger = logging.getLogger(__name__)


@dataclass
class _FallbackCliRuntime:
    """持有 fallback CLI 的单次运行状态与资源所有权。"""

    ctx: ApplicationContainer
    state: CliLoopState
    initial_toolboxes: list[Any]
    initial_prompts: list[Any]
    print_lock: threading.Lock = field(default_factory=threading.Lock)
    readline_module: Any | None = None

    def __post_init__(self) -> None:
        self.engine = self.ctx.engine
        self.registry = self.ctx.registry
        self.monitor = self.ctx.monitor
        self.channel_router = self.ctx.channel_router
        self.inbound_turns = InboundTurnCoordinator(
            self.ctx.message_queue, queue_key=lambda _message: CLI_CONVERSATION_ID
        )
        self.outbound_channels = self.ctx.outbound_channels
        self.dispatcher = OrderedOutboundDispatcher(self.outbound_channels)
        self.ctx.cli_outbound_dispatcher = self.dispatcher
        self.history_file = resolve_cli_history_file()

    def skill_toolboxes(self) -> list[Any]:
        """返回刷新后的技能工具箱，空快照时使用启动值。"""
        from miniagent.skills.snapshots import get_skill_toolboxes_from_state

        return get_skill_toolboxes_from_state(self.state) or self.initial_toolboxes

    def skill_prompts(self) -> list[Any]:
        """返回刷新后的技能提示列表。"""
        from miniagent.skills.snapshots import get_skill_prompts_from_state

        return get_skill_prompts_from_state(self.state) or self.initial_prompts

    def skill_prompt_text(self) -> str | None:
        """合并当前技能提示。"""
        from miniagent.skills.snapshots import join_skill_prompts

        return join_skill_prompts(self.skill_prompts())

    def render_width(self) -> int:
        """返回 fallback 文本布局宽度。"""
        return get_render_width(fallback_width=80)

    def print_locked(self, text: str, *, end: str = "\n") -> None:
        """串行写入 stdout，防止后台输出交错。"""
        with self.print_lock:
            print(text, end=end)
            sys.stdout.flush()

    def transcript_append(self, _style: str, text: str = "") -> None:
        """把 transcript 片段写入 stdout。"""
        if text:
            self.print_locked(text)

    def show_history(self, header: str | None = None) -> None:
        """显示当前会话首页历史。"""
        print_history_summary_fallback(
            self.state.get("session_manager"),
            self.state.get("active_session_id", ""),
            rule_heavy=lambda: print("═" * self.render_width()),
            rule_light=lambda: print("─" * self.render_width()),
            get_width=self.render_width,
            header=header,
        )

    def _thinking_inner(self, text: str, kind: str = "chunk", *, session_key: str = "") -> None:
        if text:
            self.print_locked(text, end="" if kind == "chunk" else "\n")

    def thinking_sink(
        self, text: str, kind: str = "chunk", *, session_key: str = "", **_kwargs: Any
    ) -> None:
        """按会话 turn 顺序输出思考片段。"""
        key = (session_key or "").strip() or "default"
        if self.coordinator.is_live(key):
            self._thinking_inner(text, kind, session_key=key)
        else:
            self.coordinator.defer(
                key, lambda: self._thinking_inner(text, kind, session_key=key)
            )

    def _setup_terminal(self) -> None:
        """注册 transcript、readline、出站适配器与思考 sink。"""
        from miniagent.infrastructure.cli_transcript_coordinator import CliTranscriptCoordinator

        self.coordinator = CliTranscriptCoordinator(
            self.transcript_append, None, parallel_sessions=True
        )
        self.ctx.cli_transcript_coordinator = self.coordinator
        self.ctx.cli_transcript_append = self.transcript_append
        try:
            import readline

            self.readline_module = readline
            set_history_length = getattr(readline, "set_history_length", None)
            if callable(set_history_length):
                set_history_length(1000)
            read_history_file = getattr(readline, "read_history_file", None)
            if os.path.isfile(self.history_file) and callable(read_history_file):
                read_history_file(self.history_file)
            prime_fallback_readline_history(self.history_file)
        except ImportError:
            pass
        self.outbound_channels.register(
            CliChannelAdapter(
                self._deliver_final, self._deliver_text, self._deliver_text, self._deliver_thinking
            ),
            replace=True,
        )
        self.engine.thinking.set_output_sink(self._publish_thinking)

    def _deliver_final(self, session_key: str, text: str) -> None:
        from miniagent.engine.cli_format import format_cli_reply_block

        format_cli_reply_block(
            self.coordinator.make_session_append(session_key),
            self.coordinator.make_session_append_ansi(session_key),
            text,
        )

    def _deliver_text(self, _session_key: str, text: str) -> None:
        self.print_locked(text)

    def _deliver_thinking(self, event: OutboundEvent) -> None:
        self.thinking_sink(
            event.content,
            str(event.metadata.get("fragment_kind") or "chunk"),
            session_key=event.target.conversation_id,
            ansi_markdown=event.metadata.get("ansi_markdown"),
        )

    def _publish_thinking(
        self,
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
        **_kwargs: Any,
    ) -> None:
        task = self.dispatcher.publish(
            build_cli_thinking_event(
                fragment,
                (session_key or "").strip() or "default",
                interface="fallback",
                fragment_kind=kind,
                ansi_markdown=ansi_markdown,
            )
        )
        self.ctx.register_shutdown_tracked_task(task)

    async def process_input(self, message: InboundMessage) -> None:
        """执行一个普通 Agent 输入并发送最终事件。"""
        from miniagent.engine.cli_format import format_cli_user_block
        from miniagent.engine.parallel_config import resolve_active_session_key

        user_input = message.content
        session_key = message.session_key or resolve_active_session_key(
            self.channel_router, self.state.get("active_session_id") or "default"
        )
        target = ChannelTarget(CLI_CHANNEL, session_key)
        try:
            def notify_file(message: str, _color: str = "") -> None:
                self.print_locked(message if message.endswith("\n") else message + "\n")

            user_input, _ = await process_cli_file_markers(
                user_input,
                session_key,
                self.state.get("session_manager"),
                self.ctx,
                notify=notify_file,
            )
            self.coordinator.begin_turn(session_key, source="cli")
            async with self.engine.session_turn(session_key):
                try:
                    self.print_locked("\n\n")
                    print("═" * self.render_width())
                    format_cli_user_block(
                        self.coordinator.make_session_append(session_key), user_input, [False]
                    )
                    reply = await self.engine.run_agent_with_thinking(
                        user_input,
                        session_key,
                        self.skill_toolboxes(),
                        self.skill_prompt_text(),
                        registry=self.registry,
                        monitor=self.monitor,
                        session_manager=self.state.get("session_manager"),
                        channel_router=self.channel_router,
                        clawhub=self.ctx.clawhub,
                        memory=self.ctx.memory,
                        knowledge_registry=self.ctx.knowledge_registry,
                        client=getattr(
                            self.ctx,
                            "llm_client",
                            getattr(self.ctx, "openai_client", None),
                        ),
                        cli_loop_state=self.state,
                        _hold_session_lock=True,
                    )
                    await self.dispatcher.drain(target)
                    if reply and reply.strip():
                        await self.outbound_channels.send(
                            build_cli_outbound_event(
                                reply.strip(), session_key, interface="fallback"
                            )
                        )
                finally:
                    await self.dispatcher.drain(target)
                    self.coordinator.end_turn(session_key)
        except Exception as error:
            await self.outbound_channels.send(
                build_cli_outbound_event(
                    f"{ERROR_PREFIX} 错误: {error}\n",
                    session_key,
                    interface="fallback",
                    kind=OutboundEventKind.ERROR,
                )
            )

    async def _handle_command(self, user_input: str) -> bool:
        """处理点命令；返回是否应退出主循环。"""
        from miniagent.engine.command_dispatch import dispatch_command
        from miniagent.engine.parallel_config import resolve_active_session_key

        previous_session = self.state["active_session_id"]
        result = await dispatch_command(
            user_input,
            state=self.state,
            engine=self.engine,
            registry=self.registry,
            monitor=self.monitor,
            skill_toolboxes=self.skill_toolboxes(),
            skill_prompts=self.skill_prompts(),
            capture=False,
            allow_session_mutations_when_capture=True,
            feishu_user_status=feishu_user_status_fn(self.ctx),
        )
        if self.state["active_session_id"] != previous_session:
            self.show_history("\n📜 已切换会话，最近历史如下：\n")
            prime_fallback_readline_history(self.history_file)
        if result == "__EXIT__":
            return True
        if result is not None:
            session_key = resolve_active_session_key(
                self.channel_router, self.state.get("active_session_id") or "default"
            )
            await self.outbound_channels.send(
                build_cli_outbound_event(
                    result, session_key, interface="fallback", kind=OutboundEventKind.STATUS
                )
            )
        return False

    def _copy_history(self) -> None:
        from miniagent.engine.cli_commands import build_session_history_plaintext

        plain = build_session_history_plaintext(
            self.state.get("session_manager"), self.state.get("active_session_id", "")
        )
        if plain and copy_text_to_system_clipboard(plain):
            print(f"\n{SUCCESS_PREFIX} 已复制 {len(plain)} 字符到剪贴板\n")
        elif plain:
            print(f"\n{ERROR_PREFIX} 复制失败（无可用剪贴板命令）\n")
        else:
            print("\n提示: 当前会话无历史可复制；全屏 CLI 下 /copy 复制 transcript。\n")

    async def _submit_agent(self, user_input: str) -> None:
        from miniagent.engine.parallel_config import resolve_active_session_key
        from miniagent.types.confirmation import ConfirmationResult, ConfirmationStage

        active_session = resolve_active_session_key(
            self.channel_router, self.state.get("active_session_id") or "default"
        )
        self.engine.set_active_session_key(active_session)
        confirmation = self.engine.get_confirmation_channel(active_session)
        if confirmation and confirmation.has_pending:
            if confirmation.pending.stage == ConfirmationStage.CLARIFICATION:
                confirmation.respond(ConfirmationResult.clarification_reply(user_input))
                return
        message = build_cli_inbound_message(user_input, active_session, interface="fallback")
        await self.inbound_turns.submit(message, self.process_input)
        self._maintain_runtime_files()

    def _maintain_runtime_files(self) -> None:
        if self.readline_module is not None:
            try:
                self.readline_module.write_history_file(self.history_file)
            except Exception:
                _logger.debug("fallback readline 历史写入失败", exc_info=True)
        try:
            heartbeat()
        except Exception:
            _logger.debug("fallback 心跳更新失败", exc_info=True)

    async def _handle_line(self, user_input: str) -> bool:
        """分类处理一行输入；返回是否退出。"""
        if user_input.lower() in ("quit", "exit"):
            return True
        if user_input.startswith("!"):
            command = user_input[1:].strip()
            if command:
                _, output = run_cli_shell_command(command)
                self.print_locked(output)
            return False
        if user_input == "/copy":
            self._copy_history()
            return False
        if user_input == "/stop":
            await shutdown_runtime(
                self.ctx,
                self.state,
                reason="dot_stop_fallback",
                release_cli_session_lock=True,
                call_unregister=True,
            )
            print(f"{SUCCESS_PREFIX} 当前实例已停止")
            return True
        if user_input.startswith("/"):
            return await self._handle_command(user_input)
        await self._submit_agent(user_input)
        return False

    async def run(self) -> None:
        """运行 readline 循环，直至 EOF、显式退出或停止命令。"""
        self._setup_terminal()
        self.show_history()
        try:
            while True:
                try:
                    user_input = (await asyncio.to_thread(input, "\n❯ ")).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if user_input and await self._handle_line(user_input):
                    break
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        """幂等释放本运行时拥有的终端与实例资源。"""
        from miniagent.engine.session_continue import save_cli_session_state
        from miniagent.engine.session_lock import release_session_lock

        save_cli_session_state(self.ctx, self.state)
        self.engine.thinking.set_output_sink(None)
        self.ctx.cli_transcript_append = None
        release_session_lock(self.state["active_session_id"])
        try:
            unregister_instance()
        except Exception:
            _logger.debug("fallback 实例注销失败", exc_info=True)
        print("\n\U0001f44b bye")


def print_history_summary_fallback(
    session_manager: Any,
    session_id: str,
    *,
    rule_heavy: Any,
    rule_light: Any,
    get_width: Any,
    header: str | None = None,
) -> None:
    """Print the first page of recent session history to stdout."""
    if not session_manager or not session_id:
        return
    try:
        messages, total = session_manager.load_session_history_range(
            session_id,
            start_idx=0,
            count=int(get_config("memory.initial_history_count", 5)),
        )
    except Exception as error:
        _logger.debug("fallback 历史加载失败: %s", error)
        return
    if not messages:
        return
    if header:
        print(header)

    from miniagent.engine.markdown_cli import cli_raw_markdown_enabled

    width = get_width()
    for message in messages:
        role = message.get("role", "")
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            print()
            rule_heavy()
            print("You")
            rule_light()
            print(content)
            print()
        elif role == "assistant":
            print()
            rule_light()
            print("Assistant")
            rule_light()
            if cli_raw_markdown_enabled():
                print(content)
            else:
                try:
                    from rich.console import Console
                    from rich.markdown import Markdown

                    Console(width=width).print(Markdown(content))
                except ImportError:
                    print(content)
            print()
    if total > len(messages):
        print(f"\n[… 还有 {total - len(messages)} 条更早历史]\n")
async def run_cli_loop_fallback(
    ctx: ApplicationContainer,
    state: CliLoopState,
    initial_skill_toolboxes: list[Any],
    initial_skill_prompts: list[Any],
) -> None:
    """运行无 prompt_toolkit 时的行式 CLI。"""
    await _FallbackCliRuntime(
        ctx,
        state,
        initial_skill_toolboxes,
        initial_skill_prompts,
    ).run()


__all__ = ["print_history_summary_fallback", "run_cli_loop_fallback"]
