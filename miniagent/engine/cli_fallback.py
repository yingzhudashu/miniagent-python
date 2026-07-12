"""Line-oriented CLI used when the prompt_toolkit TUI is unavailable."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
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
    """Run the line-oriented CLI with the same contracts as the TUI."""
    engine = ctx.engine
    registry = ctx.registry
    monitor = ctx.monitor
    channel_router = ctx.channel_router
    inbound_turns = InboundTurnCoordinator(
        ctx.message_queue,
        queue_key=lambda _message: CLI_CONVERSATION_ID,
    )
    outbound_channels = ctx.outbound_channels
    dispatcher = OrderedOutboundDispatcher(outbound_channels)
    ctx.cli_outbound_dispatcher = dispatcher

    from miniagent.engine.session_lock import release_session_lock
    from miniagent.skills.snapshots import (
        get_skill_prompts_from_state,
        get_skill_toolboxes_from_state,
        join_skill_prompts,
    )

    def skill_toolboxes() -> list[Any]:
        return get_skill_toolboxes_from_state(state) or initial_skill_toolboxes

    def skill_prompt_text() -> str | None:
        values = get_skill_prompts_from_state(state) or initial_skill_prompts
        return join_skill_prompts(values)

    def render_width() -> int:
        return get_render_width(fallback_width=80)

    def rule_heavy() -> None:
        print("═" * render_width())

    def rule_light() -> None:
        print("─" * render_width())

    def show_history(header: str | None = None) -> None:
        print_history_summary_fallback(
            state.get("session_manager"),
            state.get("active_session_id", ""),
            rule_heavy=rule_heavy,
            rule_light=rule_light,
            get_width=render_width,
            header=header,
        )

    print_lock = threading.Lock()

    def print_locked(text: str, *, end: str = "\n") -> None:
        with print_lock:
            print(text, end=end)
            sys.stdout.flush()

    def transcript_append(_style: str, text: str = "") -> None:
        if text:
            print_locked(text)

    from miniagent.infrastructure.cli_transcript_coordinator import (
        CliTranscriptCoordinator,
    )

    coordinator = CliTranscriptCoordinator(
        transcript_append,
        None,
        parallel_sessions=True,
    )
    ctx.cli_transcript_coordinator = coordinator
    ctx.cli_transcript_append = transcript_append

    def thinking_inner(text: str, kind: str = "chunk", *, session_key: str = "") -> None:
        if text:
            print_locked(text, end="" if kind == "chunk" else "\n")

    def thinking_sink(
        text: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        **_kwargs: Any,
    ) -> None:
        key = (session_key or "").strip() or "default"
        if coordinator.is_live(key):
            thinking_inner(text, kind, session_key=key)
        else:
            coordinator.defer(
                key,
                lambda: thinking_inner(text, kind, session_key=key),
            )

    def file_notify(message: str, _color: str = "") -> None:
        print_locked(message if message.endswith("\n") else message + "\n")

    history_file = resolve_cli_history_file()
    readline_module: Any | None = None
    try:
        import readline

        readline_module = readline
        readline_module.set_history_length(1000)
        if os.path.isfile(history_file):
            readline_module.read_history_file(history_file)
        prime_fallback_readline_history(history_file)
    except ImportError:
        pass

    show_history()

    def deliver_final(session_key: str, text: str) -> None:
        from miniagent.engine.cli_format import format_cli_reply_block

        format_cli_reply_block(
            coordinator.make_session_append(session_key),
            coordinator.make_session_append_ansi(session_key),
            text,
        )

    def deliver_text(_session_key: str, text: str) -> None:
        print_locked(text)

    def deliver_thinking(event: OutboundEvent) -> None:
        thinking_sink(
            event.content,
            str(event.metadata.get("fragment_kind") or "chunk"),
            session_key=event.target.conversation_id,
            ansi_markdown=event.metadata.get("ansi_markdown"),
        )

    outbound_channels.register(
        CliChannelAdapter(deliver_final, deliver_text, deliver_text, deliver_thinking),
        replace=True,
    )

    def publish_thinking(
        fragment: str,
        kind: str = "chunk",
        *,
        session_key: str = "",
        ansi_markdown: str | None = None,
        **_kwargs: Any,
    ) -> None:
        task = dispatcher.publish(
            build_cli_thinking_event(
                fragment,
                (session_key or "").strip() or "default",
                interface="fallback",
                fragment_kind=kind,
                ansi_markdown=ansi_markdown,
            )
        )
        ctx.register_shutdown_tracked_task(task)

    engine.thinking.set_output_sink(publish_thinking)

    async def process_input(message: InboundMessage) -> None:
        from miniagent.engine.cli_format import format_cli_user_block
        from miniagent.engine.parallel_config import resolve_active_session_key

        user_input = message.content
        session_key = message.session_key or resolve_active_session_key(
            channel_router,
            state.get("active_session_id") or "default",
        )
        target = ChannelTarget(CLI_CHANNEL, session_key)
        try:
            user_input, _ = await process_cli_file_markers(
                user_input,
                session_key,
                state.get("session_manager"),
                ctx,
                notify=file_notify,
            )
            coordinator.begin_turn(session_key, source="cli")
            async with engine.session_turn(session_key):
                try:
                    print_locked("\n\n")
                    rule_heavy()
                    format_cli_user_block(
                        coordinator.make_session_append(session_key),
                        user_input,
                        [False],
                    )
                    reply = await engine.run_agent_with_thinking(
                        user_input,
                        session_key,
                        skill_toolboxes(),
                        skill_prompt_text(),
                        registry=registry,
                        monitor=monitor,
                        session_manager=state.get("session_manager"),
                        channel_router=channel_router,
                        clawhub=ctx.clawhub,
                        memory=ctx.memory,
                        knowledge_registry=ctx.knowledge_registry,
                        client=ctx.openai_client,
                        cli_loop_state=state,
                        _hold_session_lock=True,
                    )
                    await dispatcher.drain(target)
                    if reply and reply.strip():
                        await outbound_channels.send(
                            build_cli_outbound_event(
                                reply.strip(),
                                session_key,
                                interface="fallback",
                            )
                        )
                finally:
                    try:
                        await dispatcher.drain(target)
                    finally:
                        coordinator.end_turn(session_key)
        except Exception as error:
            await outbound_channels.send(
                build_cli_outbound_event(
                    f"{ERROR_PREFIX} 错误: {error}\n",
                    session_key,
                    interface="fallback",
                    kind=OutboundEventKind.ERROR,
                )
            )

    try:
        while True:
            try:
                user_input = await asyncio.to_thread(input, "\n❯ ")
            except (EOFError, KeyboardInterrupt):
                break
            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break

            if user_input.startswith("!"):
                command = user_input[1:].strip()
                if command:
                    _, output = run_cli_shell_command(command)
                    print_locked(output)
                continue
            if user_input == "/copy":
                from miniagent.engine.cli_commands import build_session_history_plaintext

                plain = build_session_history_plaintext(
                    state.get("session_manager"),
                    state.get("active_session_id", ""),
                )
                if plain and copy_text_to_system_clipboard(plain):
                    print(f"\n{SUCCESS_PREFIX} 已复制 {len(plain)} 字符到剪贴板\n")
                elif plain:
                    print(f"\n{ERROR_PREFIX} 复制失败（无可用剪贴板命令）\n")
                else:
                    print("\n提示: 当前会话无历史可复制；全屏 CLI 下 /copy 复制 transcript。\n")
                continue
            if user_input == "/stop":
                await shutdown_runtime(
                    ctx,
                    state,
                    reason="dot_stop_fallback",
                    release_cli_session_lock=True,
                    call_unregister=True,
                )
                print(f"{SUCCESS_PREFIX} 当前实例已停止")
                break
            if user_input.startswith("/"):
                from miniagent.engine.command_dispatch import dispatch_command

                previous_session = state["active_session_id"]
                result = await dispatch_command(
                    user_input,
                    state=state,
                    engine=engine,
                    registry=registry,
                    monitor=monitor,
                    skill_toolboxes=skill_toolboxes(),
                    skill_prompts=(
                        get_skill_prompts_from_state(state) or initial_skill_prompts
                    ),
                    capture=False,
                    allow_session_mutations_when_capture=True,
                    feishu_user_status=feishu_user_status_fn(ctx),
                )
                if state["active_session_id"] != previous_session:
                    show_history("\n📜 已切换会话，最近历史如下：\n")
                    prime_fallback_readline_history(history_file)
                if result == "__EXIT__":
                    break
                if result is not None:
                    from miniagent.engine.parallel_config import resolve_active_session_key

                    session_key = resolve_active_session_key(
                        channel_router,
                        state.get("active_session_id") or "default",
                    )
                    await outbound_channels.send(
                        build_cli_outbound_event(
                            result,
                            session_key,
                            interface="fallback",
                            kind=OutboundEventKind.STATUS,
                        )
                    )
                continue

            from miniagent.engine.parallel_config import resolve_active_session_key

            active_session = resolve_active_session_key(
                channel_router,
                state.get("active_session_id") or "default",
            )
            engine.set_active_session_key(active_session)
            confirmation = engine.get_confirmation_channel(active_session)
            if confirmation and confirmation.has_pending:
                from miniagent.types.confirmation import (
                    ConfirmationResult,
                    ConfirmationStage,
                )

                if confirmation.pending.stage == ConfirmationStage.CLARIFICATION:
                    confirmation.respond(
                        ConfirmationResult.clarification_reply(user_input)
                    )
                    continue
            message = build_cli_inbound_message(
                user_input,
                active_session,
                interface="fallback",
            )
            await inbound_turns.submit(message, process_input)
            if readline_module is not None:
                try:
                    readline_module.write_history_file(history_file)
                except Exception:
                    pass
            try:
                heartbeat()
            except Exception:
                pass
    finally:
        from miniagent.engine.session_continue import save_cli_session_state

        save_cli_session_state(ctx, state)
        engine.thinking.set_output_sink(None)
        ctx.cli_transcript_append = None
        release_session_lock(state["active_session_id"])
        try:
            unregister_instance()
        except Exception:
            pass
        print("\n\U0001f44b bye")


__all__ = ["print_history_summary_fallback", "run_cli_loop_fallback"]
