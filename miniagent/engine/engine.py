"""Engine — UnifiedEngine 核心引擎

从旧版 ``unified`` 单文件拆分而来。**职责**：按 ``session_key`` 绑定 ``SessionManager`` 与会话历史；
组装技能工具箱与系统提示片段；调用 :func:`miniagent.core.agent.run_agent` 并串联 ``ThinkingDisplay``
（CLI 实时打印 / 飞书侧缓冲后卡片）；在适当时机解析 ``resolve_memory_dependencies`` 注入的
``memory_store`` / ``activity_log`` / ``keyword_index``。

**非职责**：不实现飞书 WebSocket 协议细节（见 :mod:`miniagent.feishu.poll_server`）；不解析 ``.`` 命令
（见 :mod:`miniagent.engine.command_dispatch`）。与 :mod:`miniagent.core` 的分工：core 无 asyncio 主循环与 stdin。

详见 ``docs/ARCHITECTURE.md``（UnifiedEngine 与会话管线）。
"""

from __future__ import annotations

import os
import re
from typing import Any

from miniagent.core.agent import run_agent
from miniagent.memory.context import DefaultContextManager
from miniagent.memory.defaults import resolve_memory_dependencies
from miniagent.session.manager import SessionOptions


def _fence_tool_output(body: str) -> str:
    """选用足够长的 Markdown fence，避免工具输出内含 ``` 时破坏渲染。"""
    b = (body or "").strip()
    for width in range(3, 48):
        fence = "`" * width
        opener = fence + "\n"
        closer = "\n" + fence
        if opener not in b and closer not in b and not b.endswith(fence):
            return f"{fence}\n{b}\n{fence}"
    return f"```\n{b}\n```"


def _tool_finish_verbose_history() -> bool:
    """``MINIAGENT_TOOL_FINISH_VERBOSE=1`` 时 thinking 落盘含参数与输出；默认仅工具名与成败。"""
    v = os.environ.get("MINIAGENT_TOOL_FINISH_VERBOSE", "").strip().lower()
    return v in ("1", "true", "yes")


class UnifiedEngine:
    """统一管理引擎。

    将用户输入传递给 Agent，管理会话历史和思考显示。
    集成上下文管理、跨会话记忆、活动日志。

    依赖注入：
    - registry: 工具注册表（运行时注入）
    - monitor: 性能监控器（运行时注入）
    - session_manager: 会话管理器（运行时注入）
    - feishu_config: 飞书配置（运行时注入）
    """

    def __init__(self) -> None:
        """初始化引擎：创建思考显示器、计数器、上下文管理器字典。"""
        from miniagent.engine.thinking import ThinkingDisplay

        self.thinking = ThinkingDisplay()
        self._conversation_counter: int = 0
        self._context_managers: dict[str, DefaultContextManager] = {}

    async def run_agent_with_thinking(
        self,
        user_input: str,
        session_key: str,
        skill_toolboxes: list,
        skill_prompts: str | None,
        *,
        is_feishu: bool = False,
        registry: Any = None,
        monitor: Any = None,
        session_manager: Any = None,
        feishu_config: Any = None,
        channel_router: Any = None,
        clawhub: Any | None = None,
        memory_store: Any | None = None,
        activity_log: Any | None = None,
        keyword_index: Any | None = None,
        client: Any | None = None,
        feishu_receive_chat_id: str | None = None,
        cli_loop_state: Any | None = None,
        agent_config_overrides: dict[str, Any] | None = None,
    ) -> str:
        """运行 agent 并显示思考过程。

        CLI: 终端实时显示
        飞书: 缓冲思考步骤，完成后发送

        Args:
            user_input: 用户输入
            session_key: 会话标识符
            skill_toolboxes: 可用工具箱
            skill_prompts: 技能系统提示词
            is_feishu: 当前请求是否来自飞书通道（非独立启动形态；进程始终带 CLI）
            registry: 工具注册表（注入）
            monitor: 性能监控器（注入）
            session_manager: 会话管理器（**必填**；负责 ``get_or_create``、历史持久化与会话 ``files`` 路径）
            feishu_config: 飞书配置（注入）
            channel_router: 通道路由器（飞书思考多通道回调时使用）
            clawhub: ClawHub 客户端（注入至工具上下文，技能搜索/安装复用）
            memory_store: 记忆存储（默认与 ``MINI_AGENT_STATE`` 进程 bundle 一致）
            activity_log: 活动日志（同上）
            keyword_index: 关键词索引（同上）
            client: LLM 客户端（``None`` 时由 ``run_agent`` 回落到共享工厂）
            feishu_receive_chat_id: 飞书消息 API 用的会话 ID（如群聊 ``oc_xxx``）。
                必须与 ``receive_id_type=chat_id`` 一致，**不得**传入内部路由键 ``feishu:oc_xxx``。
                缺省时若 ``session_key`` 以 ``feishu:`` 开头则自动去掉前缀（兼容旧调用）。
            cli_loop_state: 与 CLI/飞书主循环共享的 ``CliLoopState``；注入后工具 ``run_dot_command`` 可调度点命令。
            agent_config_overrides: 合并进 ``run_agent`` 的 ``agent_config``（如 ``history_progressive_compression``）。
        """
        ms, al, ki = resolve_memory_dependencies(memory_store, activity_log, keyword_index)

        if session_manager is None:
            raise ValueError(
                "run_agent_with_thinking 需要注入 session_manager（会话历史与工作区依赖 SessionManager）"
            )

        # 1. 获取会话
        session_opts = SessionOptions(
            description=f"{'飞书' if is_feishu else 'CLI'}: {session_key}"
        )
        ctx = session_manager.get_or_create(session_key, session_opts)
        history = ctx.conversation_history

        # 优先 API get_session_files_path；无则回退 Session.files_path（旧桩/测试可能无该方法）
        session_workspace = None
        getter = getattr(session_manager, "get_session_files_path", None)
        if callable(getter):
            session_workspace = getter(session_key)
        if not session_workspace:
            session_workspace = getattr(ctx, "files_path", None) or getattr(
                ctx, "workspace_path", None
            ) or None

        # 2. 技能与分层摘要进入 execute_plan 的 system（会话记忆由执行器 inject_memory 注入，避免重复）
        from miniagent.memory.memory_pipeline import build_layered_memory_augmentation

        layered_augment = build_layered_memory_augmentation(
            session_key, user_input=user_input
        )
        combined_skill = skill_prompts
        if layered_augment:
            combined_skill = (
                f"{skill_prompts}\n\n{layered_augment}" if skill_prompts else layered_augment
            )
        system_prompt = (combined_skill.strip() if combined_skill else None) or None

        # 3. 重置该会话的思考计数器（每个会话独立计数，多群并发安全）
        self.thinking.reset_counter(session_key)

        # 4. 飞书通道：启用飞书思考回调（与 CLI 终端展示并行）
        #    每个会话独立注册回调，多群聊并发时互不覆盖
        #    如果该会话有多个绑定通道（如 CLI 绑定到此），思考内容同时发送到所有通道
        if is_feishu and feishu_config:
            router = channel_router
            if router is None:
                raise ValueError("channel_router 为必填（飞书会话且提供 feishu_config 时）")
            bound_channels = router.get_bound_channels(session_key)
            # 飞书 create message 的 receive_id，须为 oc_ 等原始 ID，不能传 feishu: 前缀的内部 session_key
            im_recv = (feishu_receive_chat_id or "").strip()
            if not im_recv and session_key.startswith("feishu:"):
                im_recv = session_key[len("feishu:") :]

            async def _feishu_send(
                chat_id: str,
                text: str,
                template: str,
                *,
                is_new_round: bool = False,
                streaming: bool = True,
                merge_tools: bool = False,
                finalize_only: bool = False,
            ) -> None:
                """飞书思考：流式一轮一条卡片（PATCH 节流）；同轮工具合并时追加同卡；否则 finalize + 独立卡。"""
                from miniagent.feishu.poll_server import (
                    append_feishu_thinking_same_card,
                    finalize_feishu_thinking_stream,
                    push_feishu_thinking_stream,
                    _send_thinking,
                )

                st_local = self.thinking.thinking_state(session_key)
                if finalize_only:
                    await finalize_feishu_thinking_stream(
                        feishu_config, chat_id, template, st_local
                    )
                    return
                if streaming:
                    await push_feishu_thinking_stream(
                        feishu_config, chat_id, text, template, st_local, new_round=is_new_round
                    )
                elif merge_tools:
                    await append_feishu_thinking_same_card(
                        feishu_config, chat_id, text, template, st_local
                    )
                else:
                    await finalize_feishu_thinking_stream(feishu_config, chat_id, template, st_local)
                    await _send_thinking(feishu_config, chat_id, text, template)

            # 如果 CLI 也绑定到此会话，注册双回调（终端 + 飞书）
            if router.CLI_CHANNEL in bound_channels:
                async def _dual_send(
                    chat_id: str,
                    text: str,
                    template: str,
                    *,
                    is_new_round: bool = False,
                    streaming: bool = True,
                    merge_tools: bool = False,
                    finalize_only: bool = False,
                ) -> None:
                    """双通道：飞书仍走流式卡片；CLI 由 ThinkingDisplay._output_sink 镜像。"""
                    await _feishu_send(
                        chat_id,
                        text,
                        template,
                        is_new_round=is_new_round,
                        streaming=streaming,
                        merge_tools=merge_tools,
                        finalize_only=finalize_only,
                    )

                self.thinking.enable_feishu(session_key, im_recv, _dual_send)
            else:
                self.thinking.enable_feishu(session_key, im_recv, _feishu_send)

        # 5. 思考回调（支持流式更新；落盘到 history 的 thinking role）
        thinking_by_label: dict[str, str] = {}
        tool_thought_lines: list[str] = []

        async def _thinking(
            text: str,
            streaming: bool = False,
            header: str = "",
            *,
            full_record: str | None = None,
        ) -> None:
            record = full_record if full_record is not None else text
            if streaming:
                key = header if (header or "").strip() else "__stream__"
                thinking_by_label[key] = record
            elif record:
                hdr = (header or "").strip()
                if hdr and hdr in thinking_by_label:
                    prev = thinking_by_label[hdr]
                    thinking_by_label[hdr] = (prev + "\n" + record) if prev else record
                else:
                    tool_thought_lines.append(record)
            await self.thinking.show(
                text, session_key if is_feishu else "", streaming=streaming, header=header
            )

        async def _tool_finish(
            tool_name: str,
            args_json: str,
            result: str,
            success: bool,
            *,
            thinking_header: str = "",
        ) -> None:
            status = "成功" if success else "失败"
            short = f"`{tool_name}` · {status}"
            if _tool_finish_verbose_history():
                body = (result or "").strip()
                record = (
                    f"**工具 `{tool_name}`**（{status}）\n"
                    f"- 参数：`{args_json}`\n"
                    f"- 输出：\n{_fence_tool_output(body)}"
                )
            else:
                record = short
            await _thinking(short, False, header=thinking_header or "", full_record=record)

        # 6. 调用 Agent
        _recv_chat = (feishu_receive_chat_id or "").strip()
        if not _recv_chat and session_key.startswith("feishu:"):
            _recv_chat = session_key[len("feishu:") :].strip()
        agent_cfg_in: dict[str, Any] = {
            "session_key": session_key,
            "session_workspace": session_workspace,
            "conversation_history": history,
            "debug": False,
            "cli_loop_state": cli_loop_state,
            "cli_dispatch_allow_mutations": (not is_feishu),
            "feishu_receive_chat_id": _recv_chat or None,
        }
        if agent_config_overrides:
            agent_cfg_in.update(agent_config_overrides)
        from miniagent.core.config import get_default_agent_config, merge_agent_config

        merged_for_prog = merge_agent_config(get_default_agent_config(), agent_cfg_in)

        reply = await run_agent(
            user_input,
            registry=registry,
            monitor=monitor,
            toolboxes=skill_toolboxes,
            skip_planning=False,
            agent_config=agent_cfg_in,
            system_prompt=system_prompt,
            on_thinking=_thinking,
            on_tool_finish=_tool_finish,
            clawhub=clawhub,
            memory_store=ms,
            activity_log=al,
            keyword_index=ki,
            client=client,
        )
        # 无工具调用等场景：最后一轮 LLM 流结束后无 streaming=False，需在此 PATCH 落盘全文
        if is_feishu and feishu_config:
            from miniagent.feishu.poll_server import finalize_feishu_thinking_stream

            await finalize_feishu_thinking_stream(
                feishu_config, im_recv, "gray", self.thinking.thinking_state(session_key)
            )
        # 流式思考最后一 chunk 往往不以换行结束；否则下一区块（分隔线/回复）会黏在同一行。
        self.thinking.end_thinking()

        # 7. 飞书：思考已实时发送，清理该会话的思考状态
        if is_feishu:
            self.thinking.disable_buffer(session_key)

        # 8. 更新历史（含思考过程；会话历史不总结，仅后续可归档到日记）
        def _turn_label_sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
            lab = item[0]
            m = re.search(r"\[步骤\s*(\d+)\s*/\s*(\d+)\s*\]", lab)
            if m:
                return (0, int(m.group(1)), lab)
            if lab.startswith("[评估与计划]"):
                return (1, 0, lab)
            if lab.startswith("[执行]"):
                return (2, 0, lab)
            m = re.search(r"第\s*(\d+)\s*轮", lab)
            if m:
                return (3, int(m.group(1)), lab)
            return (4, 0, lab)

        thinking_parts: list[str] = []
        for label, blob in sorted(thinking_by_label.items(), key=_turn_label_sort_key):
            b = (blob or "").strip()
            if b:
                thinking_parts.append(f"{label}\n{b}")
        if tool_thought_lines:
            thinking_parts.append("\n".join(tool_thought_lines))
        thinking_blob = "\n\n".join(thinking_parts).strip()

        history.append({"role": "user", "content": user_input})
        if thinking_blob:
            history.append({"role": "thinking", "content": thinking_blob})
        history.append({"role": "assistant", "content": reply})
        try:
            cap = int(os.environ.get("MINI_AGENT_HISTORY_TAIL_MESSAGES", "200"))
        except ValueError:
            cap = 200

        from miniagent.memory.history_progressive import run_session_history_maintenance

        # 渐进 L1–L3 后单次归档/删轮循环，避免一次调用内多轮硬切
        run_session_history_maintenance(
            session_key,
            history,
            tail_cap=cap,
            progressive_compression=merged_for_prog.history_progressive_compression,
        )

        # 9. 活动日志
        al.log_session_start(
            session_key, user_input, source="feishu" if is_feishu else "cli"
        )
        al.log_final_reply(session_key, reply)

        # 10. 持久化
        self._conversation_counter += 1
        if session_manager:
            session_manager.save_session_history(session_key)
            self._save_numbered_history(session_key, history)

        # 11. 更新记忆存储
        try:
            from miniagent.memory.store import extract_facts, generate_turn_summary

            summary = generate_turn_summary(user_input, [], reply)
            facts = extract_facts(reply)
            await ms.update_summary(session_key, summary, facts)
        except Exception:
            pass

        try:
            from miniagent.memory.dream_scheduler import schedule_memory_maintenance

            schedule_memory_maintenance(session_key)
        except Exception:
            pass

        return reply

    def _save_numbered_history(self, session_key: str, history: list[dict]) -> None:
        """保存带编号的会话历史（已废弃）。

        不再每轮创建独立文件，改为仅更新 history.json。
        保留此方法避免旧代码调用报错。
        """
        pass

    def inject_message(self, session_key: str, content: str, *, session_manager: Any) -> None:
        """向指定会话注入消息。

        Args:
            session_key: 会话标识符
            content: 消息内容
            session_manager: 当前进程的会话管理器（由 ``RuntimeContext`` / 启动流程持有）
        """
        if session_manager:
            ctx = session_manager.get_or_create(session_key)
            ctx.conversation_history.append(
                {"role": "user", "content": content, "_injected": True}
            )

    def get_context_manager(self, session_key: str) -> DefaultContextManager | None:
        """获取会话的上下文管理器（用于 token 估算）。"""
        return self._context_managers.get(session_key)


__all__ = ["UnifiedEngine"]
