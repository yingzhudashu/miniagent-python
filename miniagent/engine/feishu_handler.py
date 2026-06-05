"""Feishu Handler — 飞书消息处理器工厂。

从 main.py 拆分，负责创建飞书消息处理器（文本/媒体）。

职责：
- 创建飞书文本消息处理器（handler）
- 创建飞书媒体消息处理器（media_handler）
- 处理飞书点命令路由
- 处理飞书私聊自动绑定
- 处理飞书媒体文件下载与处理

依赖：
- utils.py: detect_ext_from_magic, detect_mime_from_magic, feishu_user_status_fn
- cli_format.py: format_cli_user_block, format_cli_reply_block
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from miniagent.engine.cli_format import format_cli_reply_block, format_cli_user_block
from miniagent.engine.cli_state import CliLoopState
from miniagent.engine.utils import (
    detect_ext_from_magic,
    detect_mime_from_magic,
    feishu_user_status_fn,
)
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.runtime.context import RuntimeContext
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX

_logger = get_logger(__name__)

# ─── 飞书处理器工厂 ───────────────────────────────────────────


def create_feishu_handler(
    state: CliLoopState,
    ctx: RuntimeContext,
    stick_bottom: list[bool],
) -> tuple[Any, Any]:
    """创建飞书消息处理器（文本/媒体）。

    飞书消息以 `.` 开头时，路由到统一命令调度器（与 CLI 共享）。
    通过 ChannelRouter 解析 session_key：
    - 群聊消息: 始终独立会话
    - 私聊消息: 检查是否绑定到 CLI 会话（支持干预）

    技能工具箱/提示词从 ``state`` 读取，支持 ``refresh_skills`` 后无需重启飞书 handler。

    Args:
        state: CLI 循环状态（含技能快照、session_manager 等）
        ctx: 运行时上下文（engine、registry、monitor 等）
        stick_bottom: 底部粘滞状态（用于 CLI 显示）

    Returns:
        (handler, media_handler) 元组
    """
    engine = ctx.engine
    registry = ctx.registry
    monitor = ctx.monitor
    from miniagent.engine.cli_commands import feishu_dot_commands_full_enabled
    from miniagent.engine.command_dispatch import dispatch_command
    from miniagent.feishu.types import FeishuInboundText
    from miniagent.skills.snapshots import (
        get_skill_prompts_from_state,
        get_skill_toolboxes_from_state,
        join_skill_prompts,
    )

    channel_router = ctx.channel_router
    _emit_feishu_cli = feishu_user_status_fn(ctx)
    from miniagent.infrastructure.cli_feishu_policy import (
        should_allow_p2p_auto_bind,
        should_mirror_feishu_to_cli,
    )

    def _maybe_auto_bind_p2p(chat_type: str, sender_id: str, loop_state: CliLoopState) -> None:
        """私聊首条消息自动绑定到当前活跃会话。"""
        if (chat_type or "").strip().lower() != "p2p":
            return
        if not should_allow_p2p_auto_bind(channel_router):
            return
        cid = f"{channel_router.FEISHU_P2P_PREFIX}{sender_id}"
        synced: set[str] = loop_state.setdefault("feishu_p2p_synced_senders", set())  # type: ignore[assignment]
        if not isinstance(synced, set):
            synced = set()
            loop_state["feishu_p2p_synced_senders"] = synced
        if not channel_router.is_bound(cid):
            act = (loop_state.get("active_session_id") or "").strip()
            if act:
                channel_router.bind(cid, act)
                synced.add(sender_id)

    def _emit_feishu_preview(
        *,
        chat_type: str,
        chat_id: str,
        sender_id: str,
        session_key: str,
        line: str,
    ) -> None:
        """飞书消息预览输出到 CLI（若已配置镜像）。"""
        if should_mirror_feishu_to_cli(
            channel_router,
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
            session_key=session_key,
        ):
            _emit_feishu_cli(line)

    def _skill_tb() -> list:
        """获取当前技能工具箱快照。"""
        return get_skill_toolboxes_from_state(state)

    def _skill_sp() -> str | None:
        """获取当前技能提示词快照（合并）。"""
        return join_skill_prompts(get_skill_prompts_from_state(state))

    async def handler(inbound: FeishuInboundText) -> str:
        """处理单条飞书消息（:class:`~miniagent.feishu.types.FeishuInboundText`）。

        以 `.` 开头的消息路由到统一命令调度器（与 CLI 共享）。
        普通消息通过 ChannelRouter 解析 session_key 后交给 Agent 处理。
        """
        content = inbound.text
        chat_id = inbound.chat_id
        sender_id = inbound.sender_id
        chat_type = inbound.chat_type or "group"

        if not engine:
            return f"{WARNING_PREFIX} 引擎未初始化"

        # ── 命令拦截 ──
        if content.startswith("/"):
            try:
                reply = await dispatch_command(
                    content.strip(),
                    state=state,
                    engine=engine,
                    registry=registry,
                    monitor=monitor,
                    skill_toolboxes=_skill_tb(),
                    skill_prompts=get_skill_prompts_from_state(state),
                    capture=True,
                    allow_session_mutations_when_capture=feishu_dot_commands_full_enabled(),
                    message_queue_abort_chat_id=chat_id,
                )
                if reply == "__EXIT__":
                    return ""  # /stop 已通过 shutdown_runtime 清理，无需回复
                if reply is not None:
                    _maybe_auto_bind_p2p(chat_type, sender_id, state)
                    cmd_sk = channel_router.resolve_feishu_message(chat_id, sender_id, chat_type)
                    _emit_feishu_preview(
                        chat_type=chat_type,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        session_key=cmd_sk,
                        line=f"\n\U0001f4e8 [飞书命令 {chat_id[:8]}] {content}",
                    )
                    return reply
            except Exception as e:
                _logger.exception("飞书命令执行失败: %s", content)
                return f"{ERROR_PREFIX} 命令执行失败: {e}"

        _maybe_auto_bind_p2p(chat_type, sender_id, state)

        # ── 检查是否有待澄清需求（agent 正在等待用户回答）──
        cc = getattr(engine, "_confirmation_channel", None)
        if cc and cc.has_pending:
            from miniagent.types.confirmation import ConfirmationResult, ConfirmationStage

            if cc.pending.stage == ConfirmationStage.CLARIFICATION:
                cc.respond(ConfirmationResult(approved=True, adjustment=content))
                return ""  # 已回复，不启动新 agent 会话

        session_key = channel_router.resolve_feishu_message(chat_id, sender_id, chat_type)
        if (chat_id or "").strip():
            state["last_feishu_receive_chat_id"] = chat_id.strip()

        # CLI 侧：以与纯 CLI 一致的格式展示用户问题，标题中融入飞书标识
        if ctx.cli_transcript_append:
            channel_label = "飞书私聊" if chat_type == "p2p" else f"飞书 {chat_id[:8]}"
            format_cli_user_block(ctx.cli_transcript_append, content, stick_bottom, channel_label=channel_label)
        mirror_cli = should_mirror_feishu_to_cli(
            channel_router,
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
            session_key=session_key,
        )

        try:
            reply = await engine.run_agent_with_thinking(
                content,
                session_key,
                _skill_tb(),
                _skill_sp(),
                is_feishu=True,
                registry=registry,
                monitor=monitor,
                session_manager=state.get("session_manager"),
                feishu_config=ctx.feishu.get_config(),
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                feishu_receive_chat_id=chat_id,
                feishu_trigger_message_id=inbound.message_id or None,
                feishu_root_id=inbound.root_id,
                feishu_parent_id=inbound.parent_id,
                feishu_thread_id=inbound.thread_id,
                feishu_im_receive_id=(inbound.sender_id or "").strip() or None,
                cli_loop_state=state,
                feishu_mirror_cli=mirror_cli,
            )
            # 结论卡片（含质量评估尾部，与 .help 卡片一致格式）
            if reply and reply.strip():
                try:
                    from miniagent.feishu.poll_server import _send_interactive_reply_cards
                    cfg = ctx.feishu.get_config()
                    _send_interactive_reply_cards(
                        cfg, chat_id, [(reply or "").strip()],
                        reply_to_message_id=inbound.message_id or None,
                        reply_in_thread=bool((inbound.thread_id or "").strip()),
                    )
                except Exception as e:
                    _logger.debug("发送回复卡片失败: %s", e)
            # 清理 engine._last_reflection（不再发送独立卡片）
            if getattr(engine, "_last_reflection", None):
                engine._last_reflection = None

            # CLI 侧：以与纯 CLI 一致的格式展示完整回复（含质量评估，markdown 渲染）
            if ctx.cli_transcript_append and reply and reply.strip():
                format_cli_reply_block(ctx.cli_transcript_append, ctx.cli_transcript_append_ansi, (reply or "").strip())
            # 飞书单消息：思考 + 工具均在思考卡中展示，结论通过回复卡片输出。
            return ""
        except Exception as e:
            return f"{WARNING_PREFIX} 处理失败: {e}"


    async def media_handler(
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
        """下载飞书 file/image 到当前会话 workspace/files/feishu_incoming/。"""
        from miniagent.feishu.resource_io import download_message_resource, sanitize_filename
        from miniagent.types.memory import SessionOptions

        if not engine:
            return f"{WARNING_PREFIX} 引擎未初始化"

        sm = state.get("session_manager")
        if sm is None:
            return f"{WARNING_PREFIX} 会话管理器未初始化，无法保存文件"

        _maybe_auto_bind_p2p(chat_type, sender_id, state)

        session_key = channel_router.resolve_feishu_message(chat_id, sender_id, chat_type)
        sess = sm.get_or_create(
            session_key,
            SessionOptions(description="飞书媒体入站"),
        )
        base = (sess.workspace_path or "").strip()
        if not base:
            return f"{WARNING_PREFIX} 会话工作区未配置，无法写入文件"

        incoming = os.path.join(base, "feishu_incoming")
        os.makedirs(incoming, exist_ok=True)

        if resource_type not in ("file", "image"):
            return f"{WARNING_PREFIX} 不支持的资源类型"

        try:
            data, api_suggested_name = await download_message_resource(
                cfg.app_id,
                cfg.app_secret,
                message_id=message_id,
                file_key=file_key,
                type_=resource_type,
            )
        except Exception as e:
            return f"{WARNING_PREFIX} 下载失败: {e}"

        # 优先使用 API 返回的建议名（如包含原始文件名）；其次用入参名。
        # 根据文件头 magic bytes 修正扩展名，避免图片等被保存为无扩展名或 .bin。
        raw_name = (api_suggested_name or "").strip() or suggested_name
        safe = sanitize_filename(raw_name)
        root, ext = os.path.splitext(safe)
        _detected_ext = detect_ext_from_magic(data)
        if _detected_ext and ext.lower() in ("", ".bin", ".download", ".file"):
            ext = _detected_ext
            safe = root + ext
        tag = (message_id or "msg").replace("/", "_")[:16]
        dest_name = f"{root}_{tag}{ext}" if root else f"file_{tag}{ext or '.bin'}"
        dest_path = os.path.join(incoming, dest_name)

        with open(dest_path, "wb") as f:
            f.write(data)

        try:
            rel = os.path.relpath(dest_path, base)
        except ValueError:
            rel = os.path.basename(dest_path)

        # 将文件信息存储到会话记忆
        try:
            from miniagent.memory.store import add_file_to_memory
            from miniagent.types.memory import FileMetadata

            # 获取 MIME 类型
            mime_type = detect_mime_from_magic(data) or "application/octet-stream"

            file_meta = FileMetadata(
                name=dest_name,
                path=rel,
                size=len(data),
                mime_type=mime_type,
                type="image" if resource_type == "image" else ("text" if mime_type.startswith("text/") else "binary"),
                description="",  # 图片描述稍后填充
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="feishu",
            )

            # 图片描述：如果有视觉模型描述，稍后更新
            # 先添加基础信息
            await add_file_to_memory(session_key, file_meta, ctx.memory_store)
        except Exception as e:
            _logger.debug("记忆存储失败: %s", e)

        _emit_feishu_preview(
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
            session_key=session_key,
            line=f"\n\U0001f4ce [飞书媒体 {chat_id[:8]}] 已保存: {rel}",
        )
        media_mirror_cli = should_mirror_feishu_to_cli(
            channel_router,
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
            session_key=session_key,
        )

        flag = get_config("feishu.media.run_agent", False)
        run_agent_on_media = flag in ("1", "true", "yes", "on") if isinstance(flag, str) else bool(flag)
        if not run_agent_on_media:
            return f"{SUCCESS_PREFIX} 已保存到会话文件区: {rel}"

        user_line = (
            f"[飞书入站] 已保存媒体到会话目录（相对 files ）: {rel}\n"
            f"请查看该文件并说明你可以如何协助处理。"
        )
        # 图片入站：自动调用视观模型生成描述，注入对话历史
        vision_desc_enabled = get_config("feishu.media.vision_desc", True)
        if msg_type == "image" and vision_desc_enabled:
            model = get_config("model.model", "")
            if model and ctx.openai_client:
                from miniagent.feishu.vision_desc import describe_image
                desc = await describe_image(dest_path, ctx.openai_client, model)
                if desc:
                    user_line = (
                        f"[飞书入站] 用户上传了一张图片，已保存到 {rel}\n"
                        f"图片内容：{desc}"
                    )
        try:
            _ = await engine.run_agent_with_thinking(
                user_line,
                session_key,
                _skill_tb(),
                _skill_sp(),
                is_feishu=True,
                registry=registry,
                monitor=monitor,
                session_manager=sm,
                feishu_config=ctx.feishu.get_config(),
                channel_router=channel_router,
                clawhub=ctx.clawhub,
                memory_store=ctx.memory_store,
                activity_log=ctx.activity_log,
                keyword_index=ctx.keyword_index,
                client=ctx.openai_client,
                feishu_receive_chat_id=chat_id,
                feishu_trigger_message_id=message_id or None,
                feishu_thread_id=(thread_id or "").strip() or None,
                feishu_im_receive_id=(sender_id or "").strip() or None,
                cli_loop_state=state,
                feishu_mirror_cli=media_mirror_cli,
            )
            # 飞书单消息：思考 + 工具均在思考卡中展示，抑制独立回复消息。
            return ""
        except Exception as e:
            return f"{SUCCESS_PREFIX} 已保存 {rel}（Agent 处理失败: {e}）"

    return handler, media_handler


__all__ = ["create_feishu_handler"]