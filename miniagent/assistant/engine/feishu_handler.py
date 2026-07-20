"""Feishu Handler — 飞书消息处理器工厂。

从 main.py 拆分，负责创建飞书消息处理器（文本/媒体）。

职责：
- 创建飞书文本消息处理器（handler）
- 创建飞书媒体消息处理器（media_handler）
- 处理飞书 ``/`` 命令路由（与 CLI 共享 ``dispatch_command``）
- 处理飞书私聊自动绑定
- 处理飞书媒体文件下载与处理

依赖：
- command_dispatch: dispatch_command
- channel_router / cli_feishu_policy: 会话解析与 CLI 镜像策略
- poll_server._send_reply: 结论卡片发送与 text 回退
- utils.py: detect_ext_from_magic, detect_mime_from_magic, feishu_user_status_fn
- cli_format.py: format_cli_user_block, format_cli_reply_block
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.engine.cli_format import (
    format_cli_reply_block,
    format_cli_user_block,
    get_cli_format_widths,
)
from miniagent.assistant.engine.cli_inbound import CLI_CHANNEL
from miniagent.assistant.engine.cli_outbound import build_cli_outbound_event
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.utils import (
    detect_ext_from_magic,
    detect_mime_from_magic,
    feishu_user_status_fn,
)
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.ui.feishu.inbound import (
    FEISHU_CHANNEL,
    build_feishu_inbound_message,
    build_feishu_media_inbound_message,
)
from miniagent.ui.messages import ChannelTarget, OutboundEventKind

_logger = get_logger(__name__)


@dataclass
class _FeishuCliTurn:
    """飞书 agent 轮次的 CLI mirror 上下文。"""

    mirror_cli: bool
    coordinator: Any | None
    cli_append: Callable[[str, str], None] | None
    cli_append_ansi: Callable[[Any], None] | None


def _begin_feishu_cli_turn(
    ctx: ApplicationContainer,
    session_key: str,
    *,
    mirror_cli: bool,
) -> _FeishuCliTurn:
    """开启飞书轮次的 CLI transcript 上下文。

    ``mirror_cli=True`` 且存在 coordinator 时，调用 ``begin_turn`` 并绑定
    会话级 ``cli_append``；否则使用 ``ApplicationContainer`` 的全局 append。
    """
    coordinator = ctx.cli_transcript_coordinator
    if mirror_cli and coordinator is not None:
        coordinator.begin_turn(session_key, source="feishu")
    cli_append = (
        coordinator.make_session_append(session_key)
        if mirror_cli and coordinator is not None
        else ctx.cli_transcript_append
    )
    cli_append_ansi = (
        coordinator.make_session_append_ansi(session_key)
        if mirror_cli and coordinator is not None
        else ctx.cli_transcript_append_ansi
    )
    return _FeishuCliTurn(
        mirror_cli=mirror_cli,
        coordinator=coordinator,
        cli_append=cli_append,
        cli_append_ansi=cli_append_ansi,
    )


async def _drain_feishu_cli_events(ctx: ApplicationContainer, session_key: str) -> None:
    """等待当前飞书镜像会话的有序 CLI 思考事件完成。"""
    dispatcher = ctx.cli_outbound_dispatcher
    if dispatcher is not None:
        await dispatcher.drain(ChannelTarget(CLI_CHANNEL, session_key))


async def _end_feishu_cli_turn(
    ctx: ApplicationContainer,
    turn: _FeishuCliTurn,
    session_key: str,
) -> None:
    """Drain pending mirror events before ending the transcript turn."""
    try:
        if turn.mirror_cli:
            await _drain_feishu_cli_events(ctx, session_key)
    except Exception:
        _logger.warning("飞书 CLI 镜像思考事件收尾失败", exc_info=True)
    finally:
        if turn.mirror_cli and turn.coordinator is not None:
            turn.coordinator.end_turn(session_key)


async def _render_feishu_cli_reply(
    ctx: ApplicationContainer,
    turn: _FeishuCliTurn,
    session_key: str,
    reply: str,
    state: CliLoopState,
) -> None:
    """Render a mirrored final reply through the CLI adapter."""
    if not turn.mirror_cli or not turn.cli_append or not reply:
        return
    channels = ctx.outbound_channels
    try:
        channels.get(CLI_CHANNEL)
    except LookupError:
        pass
    else:
        await channels.send(
            build_cli_outbound_event(reply, session_key, interface="feishu-mirror")
        )
        return
    render_width, markdown_width = get_cli_format_widths(state)
    format_cli_reply_block(
        turn.cli_append,
        turn.cli_append_ansi,
        reply,
        render_width=render_width,
        markdown_width=markdown_width,
    )


async def _send_feishu_agent_reply(
    cfg: Any,
    chat_id: str,
    reply: str,
    *,
    reply_to_message_id: str | None = None,
    reply_in_thread: bool = False,
) -> None:
    """发送 Agent 结论到飞书（交互卡片 + 失败时 text 回退）。

    委托 ``poll_server._send_reply``，与 poll_server 出站路径行为一致。
    """
    from miniagent.assistant.feishu.poll_server import _send_reply

    body = (reply or "").strip()
    if not body:
        return
    try:
        await _send_reply(
            cfg,
            chat_id,
            body,
            reply_to_message_id=reply_to_message_id,
            reply_in_thread=reply_in_thread,
        )
    except Exception as e:
        _logger.warning("发送飞书 Agent 结论失败，将由 handler 返回文本: %s", e)
        raise


class _FeishuHandlerRuntime:
    """拥有飞书文本/媒体处理器依赖和单轮适配策略。"""

    def __init__(
        self, state: CliLoopState, ctx: ApplicationContainer, stick_bottom: list[bool]
    ) -> None:
        self.state = state
        self.ctx = ctx
        self.stick_bottom = stick_bottom
        self.engine = ctx.engine
        self.registry = ctx.registry
        self.monitor = ctx.monitor
        self.channel_router = ctx.channel_router
        self.outbound_channels = ctx.outbound_channels
        self.emit_cli = feishu_user_status_fn(ctx)
        self._register_channel()

    def _register_channel(self) -> None:
        from miniagent.ui.feishu.outbound import FeishuChannelAdapter

        async def send_final(chat_id: str, text: str, message_id: str | None, in_thread: bool) -> None:
            await _send_feishu_agent_reply(
                self.ctx.feishu.get_config(),
                chat_id,
                text,
                reply_to_message_id=message_id,
                reply_in_thread=in_thread,
            )

        try:
            self.outbound_channels.get(FEISHU_CHANNEL)
        except LookupError:
            self.outbound_channels.register(FeishuChannelAdapter(send_final))

    def skill_toolboxes(self) -> list[Any]:
        from miniagent.assistant.skills.snapshots import get_skill_toolboxes_from_state

        return get_skill_toolboxes_from_state(self.state)

    def skill_prompts(self) -> list[Any]:
        from miniagent.assistant.skills.snapshots import get_skill_prompts_from_state

        return get_skill_prompts_from_state(self.state)

    def skill_prompt_text(self) -> str | None:
        from miniagent.assistant.skills.snapshots import join_skill_prompts

        return join_skill_prompts(self.skill_prompts())

    def maybe_auto_bind(self, chat_type: str, sender_id: str) -> None:
        """在策略允许时将私聊首条消息绑定到活跃 CLI 会话。"""
        from miniagent.assistant.infrastructure.cli_feishu_policy import should_allow_p2p_auto_bind

        if chat_type.strip().lower() != "p2p" or not should_allow_p2p_auto_bind(
            self.channel_router
        ):
            return
        channel_id = f"{self.channel_router.FEISHU_P2P_PREFIX}{sender_id}"
        synced = self.state.setdefault("feishu_p2p_synced_senders", set())
        if not isinstance(synced, set):
            synced = set()
            self.state["feishu_p2p_synced_senders"] = synced
        if self.channel_router.is_bound(channel_id):
            return
        active = (self.state.get("active_session_id") or "").strip()
        if active:
            self.channel_router.bind(channel_id, active)
            synced.add(sender_id)

    def mirror_enabled(
        self, chat_type: str, chat_id: str, sender_id: str, session_key: str
    ) -> bool:
        from miniagent.assistant.infrastructure.cli_feishu_policy import should_mirror_feishu_to_cli

        return should_mirror_feishu_to_cli(
            self.channel_router,
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
            session_key=session_key,
        )

    def emit_preview(
        self,
        chat_type: str,
        chat_id: str,
        sender_id: str,
        session_key: str,
        line: str,
    ) -> None:
        if self.mirror_enabled(chat_type, chat_id, sender_id, session_key):
            self.emit_cli(line)

    async def send_reply(
        self,
        kind: OutboundEventKind,
        text: str,
        *,
        chat_id: str,
        message_id: str,
        thread_id: str | None,
    ) -> str:
        from miniagent.ui.feishu.outbound import build_feishu_reply_event

        await self.outbound_channels.send(
            build_feishu_reply_event(
                kind,
                text,
                chat_id,
                reply_to_message_id=message_id or None,
                thread_id=thread_id,
                trace_id=message_id or None,
            )
        )
        return ""

    async def _handle_command(self, inbound: Any) -> tuple[bool, str]:
        """执行飞书点命令；返回是否已处理及其发送结果。"""
        if not inbound.text.startswith("/"):
            return False, ""
        from miniagent.assistant.engine.command_dispatch import dispatch_command
        from miniagent.assistant.engine.commands.session_management import (
            feishu_dot_commands_full_enabled,
        )

        session_key = self.channel_router.resolve_feishu_message(
            inbound.chat_id, inbound.sender_id, inbound.chat_type or "group"
        )
        message = build_feishu_inbound_message(inbound, session_key)
        try:
            reply = await dispatch_command(
                message.content.strip(),
                state=self.state,
                engine=self.engine,
                registry=self.registry,
                monitor=self.monitor,
                skill_toolboxes=self.skill_toolboxes(),
                skill_prompts=self.skill_prompts(),
                capture=True,
                allow_session_mutations_when_capture=feishu_dot_commands_full_enabled(),
                message_queue_abort_chat_id=inbound.chat_id,
                confirmation_session_key=session_key,
            )
            if reply == "__EXIT__":
                return True, ""
            if reply is None:
                return True, ""
            self.maybe_auto_bind(inbound.chat_type or "group", inbound.sender_id)
            self.emit_preview(
                inbound.chat_type or "group",
                inbound.chat_id,
                inbound.sender_id,
                session_key,
                f"\n📨 [飞书命令 {inbound.chat_id[:8]}] {message.content}",
            )
            result = await self.send_reply(
                OutboundEventKind.STATUS,
                reply,
                chat_id=message.conversation_id,
                message_id=str(message.metadata.get("message_id") or ""),
                thread_id=message.thread_id,
            )
            return True, result
        except Exception as error:
            _logger.exception("飞书命令执行失败: %s", inbound.text)
            result = await self.send_reply(
                OutboundEventKind.ERROR,
                f"{ERROR_PREFIX} 命令执行失败: {error}",
                chat_id=inbound.chat_id,
                message_id=(inbound.message_id or "").strip(),
                thread_id=inbound.thread_id,
            )
            return True, result

    async def handler(self, inbound: Any) -> str:
        """处理一条飞书文本/交互消息。"""
        if not self.engine:
            return f"{WARNING_PREFIX} 引擎未初始化"
        handled, result = await self._handle_command(inbound)
        if handled:
            return result
        self.maybe_auto_bind(inbound.chat_type or "group", inbound.sender_id)
        session_key = self.channel_router.resolve_feishu_message(
            inbound.chat_id, inbound.sender_id, inbound.chat_type or "group"
        )
        message = build_feishu_inbound_message(inbound, session_key)
        confirmation = self.engine.get_confirmation_channel(session_key)
        if self._respond_clarification(confirmation, message.content):
            return ""
        if message.conversation_id.strip():
            self.state["last_feishu_receive_chat_id"] = message.conversation_id.strip()
        mirror = self.mirror_enabled(
            str(message.metadata.get("chat_type") or "group"),
            message.conversation_id,
            message.sender_id,
            session_key,
        )
        return await self._run_text_turn(message, session_key, mirror)

    @staticmethod
    def _respond_clarification(channel: Any, content: str) -> bool:
        if not channel or not channel.has_pending:
            return False
        from miniagent.agent.types.confirmation import ConfirmationResult, ConfirmationStage

        if channel.pending.stage != ConfirmationStage.CLARIFICATION:
            return False
        channel.respond(ConfirmationResult.clarification_reply(content))
        return True

    async def _run_text_turn(self, message: Any, session_key: str, mirror: bool) -> str:
        turn = _begin_feishu_cli_turn(self.ctx, session_key, mirror_cli=mirror)
        async with self.engine.session_turn(session_key):
            try:
                chat_type = str(message.metadata.get("chat_type") or "group")
                if turn.mirror_cli and turn.cli_append:
                    label = "飞书私聊" if chat_type == "p2p" else f"飞书 {message.conversation_id[:8]}"
                    width, _ = get_cli_format_widths(self.state)
                    format_cli_user_block(
                        turn.cli_append,
                        message.content,
                        self.stick_bottom,
                        channel_label=label,
                        render_width=width,
                    )
                reply = await self._run_agent(
                    message.content,
                    session_key,
                    message,
                    mirror,
                    self.state.get("session_manager"),
                )
                return await self._finish_agent_turn(message, session_key, turn, reply)
            except Exception as error:
                return await self.send_reply(
                    OutboundEventKind.ERROR,
                    f"{WARNING_PREFIX} 处理失败: {error}",
                    chat_id=message.conversation_id,
                    message_id=str(message.metadata.get("message_id") or ""),
                    thread_id=message.thread_id,
                )
            finally:
                await _end_feishu_cli_turn(self.ctx, turn, session_key)

    async def _run_agent(
        self, content: str, session_key: str, message: Any, mirror: bool, session_manager: Any
    ) -> str:
        return await self.engine.run_agent_with_thinking(
            content,
            session_key,
            self.skill_toolboxes(),
            self.skill_prompt_text(),
            is_feishu=True,
            registry=self.registry,
            monitor=self.monitor,
            session_manager=session_manager,
            feishu_config=self.ctx.feishu.get_config(),
            channel_router=self.channel_router,
            clawhub=self.ctx.clawhub,
            memory=self.ctx.memory,
            knowledge_registry=self.ctx.knowledge_registry,
            client=getattr(
                self.ctx, "llm_client", getattr(self.ctx, "llm_gateway", None)
            ),
            feishu_receive_chat_id=message.conversation_id,
            feishu_trigger_message_id=str(message.metadata.get("message_id") or "") or None,
            feishu_root_id=message.metadata.get("root_id") if isinstance(message.metadata.get("root_id"), str) else None,
            feishu_parent_id=message.metadata.get("parent_id") if isinstance(message.metadata.get("parent_id"), str) else None,
            feishu_thread_id=message.thread_id,
            feishu_im_receive_id=(message.sender_id or "").strip() or None,
            cli_loop_state=self.state,
            feishu_mirror_cli=mirror,
            _hold_session_lock=True,
        )

    async def _finish_agent_turn(
        self, message: Any, session_key: str, turn: _FeishuCliTurn, reply: str
    ) -> str:
        from miniagent.ui.feishu.outbound import build_feishu_final_event

        if turn.mirror_cli:
            await _drain_feishu_cli_events(self.ctx, session_key)
        body = (reply or "").strip()
        failed = False
        if body:
            try:
                await self.outbound_channels.send(
                    build_feishu_final_event(
                        body,
                        message.conversation_id,
                        reply_to_message_id=str(message.metadata.get("message_id") or "") or None,
                        thread_id=message.thread_id,
                        trace_id=message.trace_id,
                    )
                )
            except Exception:
                failed = True
        self.engine.clear_last_reflection(session_key)
        await _render_feishu_cli_reply(self.ctx, turn, session_key, body, self.state)
        return body if failed else ""

    async def media_handler(
        self,
        cfg: Any,
        message_id: str,
        chat_id: str,
        sender_id: str,
        chat_type: str,
        msg_type: str,
        file_key: str,
        suggested_name: str,
        resource_type: str,
        thread_id: str | None = None,
    ) -> str | None:
        """下载飞书媒体、登记会话记忆，并按配置触发 Agent。"""
        if not self.engine:
            return f"{WARNING_PREFIX} 引擎未初始化"
        session_manager = self.state.get("session_manager")
        if session_manager is None:
            return f"{WARNING_PREFIX} 会话管理器未初始化，无法保存文件"
        self.maybe_auto_bind(chat_type, sender_id)
        session_key = self.channel_router.resolve_feishu_message(
            chat_id, sender_id, chat_type
        )
        workspace = self._media_workspace(session_manager, session_key)
        if not workspace:
            return f"{WARNING_PREFIX} 会话工作区未配置，无法写入文件"
        if resource_type not in ("file", "image"):
            return f"{WARNING_PREFIX} 不支持的资源类型"
        try:
            saved = await self._download_media(
                cfg,
                workspace,
                message_id,
                file_key,
                suggested_name,
                cast(Literal["file", "image"], resource_type),
            )
        except Exception as error:
            return f"{WARNING_PREFIX} 下载失败: {error}"
        path, relative_path, filename, data, mime_type = saved
        await self._remember_media(
            session_key, relative_path, filename, data, mime_type, resource_type
        )
        self.emit_preview(
            chat_type,
            chat_id,
            sender_id,
            session_key,
            f"\n📎 [飞书媒体 {chat_id[:8]}] 已保存: {relative_path}",
        )
        if not self._media_runs_agent():
            return f"{SUCCESS_PREFIX} 已保存到会话文件区: {relative_path}"
        content = await self._media_prompt(msg_type, path, relative_path)
        message = build_feishu_media_inbound_message(
            content=content,
            session_key=session_key,
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            chat_type=chat_type,
            msg_type=msg_type,
            file_key=file_key,
            resource_type=resource_type,
            name=filename,
            mime_type=mime_type,
            size=len(data),
            local_path=path,
            relative_path=relative_path,
            thread_id=thread_id,
        )
        mirror = self.mirror_enabled(chat_type, chat_id, sender_id, session_key)
        return await self._run_media_turn(
            message, session_key, mirror, session_manager, relative_path
        )

    @staticmethod
    def _media_workspace(session_manager: Any, session_key: str) -> str:
        from miniagent.agent.types.memory import SessionOptions

        session = session_manager.get_or_create(
            session_key, SessionOptions(description="飞书媒体入站")
        )
        return (session.workspace_path or "").strip()

    async def _download_media(
        self,
        cfg: Any,
        workspace: str,
        message_id: str,
        file_key: str,
        suggested_name: str,
        resource_type: Literal["file", "image"],
    ) -> tuple[str, str, str, bytes, str]:
        """下载飞书媒体并以去冲突名称持久化到会话工作区。"""
        from miniagent.assistant.feishu.resource_io import (
            download_message_resource,
            sanitize_filename,
        )

        data, api_name = await download_message_resource(
            cfg.app_id,
            cfg.app_secret,
            message_id=message_id,
            file_key=file_key,
            type_=resource_type,
        )
        incoming = os.path.join(workspace, "feishu_incoming")
        os.makedirs(incoming, exist_ok=True)
        safe_name = sanitize_filename((api_name or "").strip() or suggested_name)
        root, extension = os.path.splitext(safe_name)
        detected = detect_ext_from_magic(data)
        if detected and extension.lower() in ("", ".bin", ".download", ".file"):
            extension = detected
        tag = (message_id or "msg").replace("/", "_")[:16]
        filename = f"{root}_{tag}{extension}" if root else f"file_{tag}{extension or '.bin'}"
        path = os.path.join(incoming, filename)

        def write() -> None:
            with open(path, "wb") as file:
                file.write(data)

        await asyncio.to_thread(write)
        try:
            relative = os.path.relpath(path, workspace)
        except ValueError:
            relative = os.path.basename(path)
        return path, relative, filename, data, detect_mime_from_magic(data) or "application/octet-stream"

    async def _remember_media(
        self,
        session_key: str,
        relative_path: str,
        filename: str,
        data: bytes,
        mime_type: str,
        resource_type: str,
    ) -> None:
        try:
            from miniagent.agent.types.memory import FileMetadata
            from miniagent.assistant.memory.store import add_file_to_memory

            metadata = FileMetadata(
                name=filename,
                path=relative_path,
                size=len(data),
                mime_type=mime_type,
                type="image" if resource_type == "image" else ("text" if mime_type.startswith("text/") else "binary"),
                description="",
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="feishu",
            )
            await add_file_to_memory(session_key, metadata, self.ctx.memory.store)
        except Exception as error:
            _logger.debug("记忆存储失败: %s", error)

    @staticmethod
    def _media_runs_agent() -> bool:
        value = get_config("feishu.media.run_agent", False)
        return value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)

    async def _media_prompt(self, message_type: str, path: str, relative_path: str) -> str:
        default = (
            f"[飞书入站] 已保存媒体到会话文件区: {relative_path}\n"
            "请查看该文件并说明你可以如何协助处理。"
        )
        if message_type != "image" or not get_config("feishu.media.vision_desc", True):
            return default
        llm_client = getattr(
            self.ctx, "llm_client", getattr(self.ctx, "llm_gateway", None)
        )
        if not llm_client:
            return default
        from miniagent.assistant.feishu.vision_desc import describe_image

        description = await describe_image(path, llm_client)
        return (
            f"[飞书入站] 用户上传了一张图片，已保存到 {relative_path}\n图片内容：{description}"
            if description
            else default
        )

    async def _run_media_turn(
        self,
        message: Any,
        session_key: str,
        mirror: bool,
        session_manager: Any,
        relative_path: str,
    ) -> str:
        turn = _begin_feishu_cli_turn(self.ctx, session_key, mirror_cli=mirror)
        async with self.engine.session_turn(session_key):
            try:
                if turn.mirror_cli and turn.cli_append:
                    chat_type = str(message.metadata.get("chat_type") or "group")
                    label = "飞书私聊媒体" if chat_type == "p2p" else f"飞书媒体 {message.conversation_id[:8]}"
                    width, _ = get_cli_format_widths(self.state)
                    format_cli_user_block(
                        turn.cli_append,
                        message.content,
                        self.stick_bottom,
                        channel_label=label,
                        render_width=width,
                    )
                reply = await self._run_agent(
                    message.content, session_key, message, mirror, session_manager
                )
                result = await self._finish_agent_turn(message, session_key, turn, reply)
                return f"{SUCCESS_PREFIX} 已保存 {relative_path}\n\n{result}" if result else ""
            except Exception as error:
                return await self.send_reply(
                    OutboundEventKind.ERROR,
                    f"{SUCCESS_PREFIX} 已保存 {relative_path}（Agent 处理失败: {error}）",
                    chat_id=message.conversation_id,
                    message_id=str(message.metadata.get("message_id") or ""),
                    thread_id=message.thread_id,
                )
            finally:
                await _end_feishu_cli_turn(self.ctx, turn, session_key)


# ─── 飞书处理器工厂 ───────────────────────────────────────────


def create_feishu_handler(
    state: CliLoopState,
    ctx: ApplicationContainer,
    stick_bottom: list[bool],
) -> tuple[Any, Any]:
    """创建飞书消息处理器（文本/媒体）。

    飞书消息以 ``/`` 开头时，路由到统一命令调度器（与 CLI 共享）。
    通过 ChannelRouter 解析 session_key：
    - 群聊消息: 始终独立会话
    - 私聊消息: 检查是否绑定到 CLI 会话（支持干预）

    普通 Agent 轮次成功后 handler 通常返回空串（结论经交互卡片或 text 回退发出）；
    命令路径与异常路径返回非空字符串，由 ``poll_server`` 作 text 回复。

    技能工具箱/提示词从 ``state`` 读取，支持 ``refresh_skills`` 后无需重启飞书 handler。

    Args:
        state: CLI 循环状态（含技能快照、session_manager 等）
        ctx: 运行时上下文（engine、registry、monitor 等）
        stick_bottom: 底部粘滞状态（用于 CLI 显示）

    Returns:
        ``(handler, media_handler)`` 元组
    """
    runtime = _FeishuHandlerRuntime(state, ctx, stick_bottom)
    return runtime.handler, runtime.media_handler
__all__ = ["create_feishu_handler"]
