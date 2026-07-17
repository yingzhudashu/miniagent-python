"""全屏 TUI 单轮 Agent 执行与渠道投递。"""

from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit.application import get_app

from miniagent.agent.types.error_prefix import ERROR_PREFIX
from miniagent.assistant.engine.cli_files import process_cli_file_markers
from miniagent.assistant.engine.cli_inbound import CLI_CHANNEL
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.ui.messages import ChannelTarget, InboundMessage


class _TuiTurnProcessor:
    """拥有一轮 TUI 输入所需依赖并执行规范化入站消息。"""

    channel_router: Any
    state: CliLoopState | dict[str, Any]
    ctx: Any
    term_write: Any
    coordinator: Any
    engine: Any
    cli_rule_heavy: Any
    output_at_bottom: Any
    stick_bottom: list[bool]
    snap_output_bottom: Any
    rule_line_width: Any
    skill_toolboxes: Any
    skill_prompts: Any
    registry: Any
    monitor: Any
    dispatcher: Any
    outbound_channels: Any
    build_event: Any
    outbound_event_kind: Any

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)

    async def __call__(self, message: InboundMessage) -> None:
        """渲染用户块、串行执行 Agent 并投递结果。"""
        from miniagent.assistant.engine.cli_format import format_cli_user_block
        from miniagent.assistant.engine.parallel_config import resolve_active_session_key

        user_input = message.content
        session_key = message.session_key or resolve_active_session_key(
            self.channel_router, self.state.get("active_session_id") or "default"
        )
        target = ChannelTarget(CLI_CHANNEL, session_key)
        try:
            user_input, _ = await process_cli_file_markers(
                user_input,
                session_key,
                self.state.get("session_manager"),
                self.ctx,
                notify=self.term_write,
            )
            self.coordinator.begin_turn(session_key, source="cli")
            cli_append = self.coordinator.make_session_append(session_key)
            async with self.engine.session_turn(session_key):
                try:
                    self.cli_rule_heavy()
                    was_at_bottom = self.output_at_bottom()
                    self.stick_bottom[0] = True
                    self._snap_and_invalidate()
                    format_cli_user_block(
                        cli_append,
                        user_input,
                        self.stick_bottom,
                        render_width=self.rule_line_width(),
                    )
                    await asyncio.sleep(0)
                    if was_at_bottom:
                        self.stick_bottom[0] = True
                        self._snap_and_invalidate()
                    reply = await self._run_agent(user_input, session_key)
                    await self.dispatcher.drain(target)
                    if reply and reply.strip():
                        await self.outbound_channels.send(
                            self.build_event(reply.strip(), session_key, interface="tui")
                        )
                finally:
                    try:
                        await self.dispatcher.drain(target)
                    finally:
                        self.coordinator.end_turn(session_key)
        except Exception as error:
            await self.outbound_channels.send(
                self.build_event(
                    f"{ERROR_PREFIX} 错误: {error}\n",
                    session_key,
                    interface="tui",
                    kind=self.outbound_event_kind.ERROR,
                )
            )

    def _snap_and_invalidate(self) -> None:
        """尽力滚动到底部并刷新 prompt_toolkit。"""
        try:
            self.snap_output_bottom()
            get_app().invalidate()
        except Exception:
            pass

    async def _run_agent(self, user_input: str, session_key: str) -> str:
        """调用统一引擎并显式注入本轮依赖。"""
        return await self.engine.run_agent_with_thinking(
            user_input,
            session_key,
            self.skill_toolboxes(),
            self.skill_prompts(),
            registry=self.registry,
            monitor=self.monitor,
            session_manager=self.state.get("session_manager"),
            channel_router=self.channel_router,
            clawhub=self.ctx.clawhub,
            memory=self.ctx.memory,
            knowledge_registry=self.ctx.knowledge_registry,
            client=getattr(
                self.ctx, "llm_client", getattr(self.ctx, "llm_gateway", None)
            ),
            cli_loop_state=self.state,
            _hold_session_lock=True,
        )


def create_tui_process_input(
    *,
    channel_router: Any,
    state: CliLoopState | dict[str, Any],
    runtime_context: Any,
    term_write: Any,
    transcript_coordinator: Any,
    engine: Any,
    cli_rule_heavy: Any,
    output_at_bottom: Any,
    stick_bottom: list[bool],
    snap_output_bottom: Any,
    rule_line_width: Any,
    skill_toolboxes: Any,
    skill_prompts: Any,
    registry: Any,
    monitor: Any,
    cli_outbound_dispatcher: Any,
    outbound_channels: Any,
    build_cli_outbound_event: Any,
    outbound_event_kind: Any,
) -> Any:
    """构造规范化 CLI 入站消息的异步处理器。"""
    return _TuiTurnProcessor(
        channel_router=channel_router,
        state=state,
        ctx=runtime_context,
        term_write=term_write,
        coordinator=transcript_coordinator,
        engine=engine,
        cli_rule_heavy=cli_rule_heavy,
        output_at_bottom=output_at_bottom,
        stick_bottom=stick_bottom,
        snap_output_bottom=snap_output_bottom,
        rule_line_width=rule_line_width,
        skill_toolboxes=skill_toolboxes,
        skill_prompts=skill_prompts,
        registry=registry,
        monitor=monitor,
        dispatcher=cli_outbound_dispatcher,
        outbound_channels=outbound_channels,
        build_event=build_cli_outbound_event,
        outbound_event_kind=outbound_event_kind,
    )


__all__ = ["create_tui_process_input"]
