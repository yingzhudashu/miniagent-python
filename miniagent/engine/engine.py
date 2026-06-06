"""Engine — UnifiedEngine 核心引擎

从原 ``unified`` 单文件拆分而来。**职责**：按 ``session_key`` 绑定 ``SessionManager`` 与会话历史；
组装技能工具箱与系统提示片段；调用 :func:`miniagent.core.agent.run_agent` 并串联 ``ThinkingDisplay``
（CLI 实时打印 / 飞书侧缓冲后卡片）；在适当时机解析 ``resolve_memory_dependencies`` 注入的
``memory_store`` / ``activity_log`` / ``keyword_index``。

**非职责**：不实现飞书 WebSocket 协议细节（见 :mod:`miniagent.feishu.poll_server`）；不解析 ``.`` 命令
（见 :mod:`miniagent.engine.command_dispatch`）。与 :mod:`miniagent.core` 的分工：core 无 asyncio 主循环与 stdin。

**并发与队列**：CLI 与飞书共用同一进程时，「执行一轮 :meth:`run_agent_with_thinking`」的调度仍应由
:class:`miniagent.infrastructure.message_queue.MessageQueueManager` 按 ``chat_id`` 串行或抢占；本类不替代队列，
仅在被调用的协程内完成单次回合的 LLM/工具编排。

详见 ``docs/ARCHITECTURE.md``（UnifiedEngine 与会话管线）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

# 性能优化：预编译高频正则表达式
_STEP_NUMBER_PATTERN = re.compile(r"\[步骤\s*(\d+)\s*/\s*(\d+)\s*\]")
_ROUND_NUMBER_PATTERN = re.compile(r"第\s*(\d+)\s*轮")

from miniagent.core.agent import run_agent
from miniagent.engine.cli_commands import feishu_dot_commands_full_enabled
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.memory.defaults import resolve_memory_dependencies
from miniagent.session.manager import SessionOptions

_logger = get_logger(__name__)


def _fence_tool_output(body: str) -> str:
    """选用足够长的 Markdown fence，避免工具输出内含 ``` 时破坏渲染。

    从 3 个反引号开始尝试，逐步增加 fence 长度直到找到一个不会与输出内容冲突的长度。
    最大尝试到 47 个反引号，超出则使用默认 3 个反引号。

    Args:
        body: 工具输出正文（可能包含 Markdown fence）

    Returns:
        str: 用合适长度 fence 包裹的 Markdown 代码块

    Note:
        - 用于 CLI 和飞书卡片中的工具输出显示
        - 避免输出内容中的 ``` 序列与 fence 冲突导致渲染异常
    """
    b = (body or "").strip()
    for width in range(3, 48):
        fence = "`" * width
        opener = fence + "\n"
        closer = "\n" + fence
        if opener not in b and closer not in b and not b.endswith(fence):
            return f"{fence}\n{b}\n{fence}"
    return f"```\n{b}\n```"


def _tool_finish_verbose_history() -> bool:
    """检查是否在工具完成回调中记录详细历史。

    Returns:
        bool: True 时 on_tool_finish 落盘含参数与输出；
              False（默认）时仅记录工具名与成败状态

    Note:
        - 配置项: execution.tool_finish_verbose
        - 详细模式下会增加历史文件体积，用于调试或审计
    """
    from miniagent.core.constants import EXECUTION_TOOL_FINISH_VERBOSE

    return EXECUTION_TOOL_FINISH_VERBOSE


class UnifiedEngine:
    """统一管理引擎。

    将用户输入传递给 Agent，管理会话历史和思考显示。
    集成上下文管理、跨会话记忆、活动日志。

    运行参数（均通过 :meth:`run_agent_with_thinking` 注入）：
    - registry: 工具注册表
    - monitor: 性能监控器
    - session_manager: 会话管理器（必填）
    - feishu_config: 飞书配置
    """

    def __init__(self) -> None:
        """初始化引擎：创建思考显示器、懒加载澄清器、进程级执行锁。"""
        from miniagent.engine.thinking import ThinkingDisplay

        self.thinking = ThinkingDisplay()
        self._clarifier: Any | None = None
        self._exec_lock = asyncio.Lock()
        self._confirmation_channel: Any | None = None
        self._last_reflection: Any | None = None

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
        feishu_trigger_message_id: str | None = None,
        feishu_root_id: str | None = None,
        feishu_parent_id: str | None = None,
        feishu_thread_id: str | None = None,
        feishu_im_receive_id_type: str | None = None,
        feishu_im_receive_id: str | None = None,
        cli_loop_state: Any | None = None,
        agent_config_overrides: dict[str, Any] | None = None,
        feishu_mirror_cli: bool = True,
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
            memory_store: 记忆存储（默认与 ``MINIAGENT_PATHS_STATE_DIR`` 进程 bundle 一致）
            activity_log: 活动日志（同上）
            keyword_index: 关键词索引（同上）
            client: LLM 客户端（``None`` 时由 ``run_agent`` 回落到共享工厂）
            feishu_receive_chat_id: 飞书消息 API 用的会话 ID（如群聊 ``oc_xxx``）。
                必须与 ``receive_id_type=chat_id`` 一致，**不得**传入内部路由键 ``feishu:oc_xxx``。
                缺省时若 ``session_key`` 以 ``feishu:`` 开头则自动去掉前缀。
            feishu_trigger_message_id: 入站飞书 ``message_id``（可选；供 AgentConfig 与 ``MINIAGENT_FEISHU_REPLY_TARGET=reply``）。
            feishu_root_id: 入站 ``root_id``（可选）。
            feishu_parent_id: 入站 ``parent_id``（可选）。
            feishu_thread_id: 入站 ``thread_id``（可选；与 ``MINIAGENT_FEISHU_REPLY_TARGET=reply`` 及未显式设置
                ``MINIAGENT_FEISHU_REPLY_IN_THREAD`` 时是否默认话题内回复有关）。
            feishu_im_receive_id_type: 飞书 IM 发消息 ``receive_id_type``（``chat_id`` / ``open_id`` / ``union_id``）；缺省由执行器读环境变量。
            feishu_im_receive_id: 非 ``chat_id`` 时作为默认 ``receive_id``（通常为入站发送者 ``open_id``）。
            cli_loop_state: 与 CLI/飞书主循环共享的 ``CliLoopState``；注入后工具 ``run_dot_command`` 可调度点命令。
            agent_config_overrides: 合并进 ``run_agent`` 的 ``agent_config``（如 ``history_progressive_compression``）。
        """
        if session_manager is None:
            raise ValueError(
                "run_agent_with_thinking 需要注入 session_manager（会话历史与工作区依赖 SessionManager）"
            )

        # 获取进程级执行锁：防止 CLI 和飞书（或不同飞书群）的并发调用导致
        # 终端输出穿插、ThinkingDisplay 状态混乱、会话历史竞争
        async with self._exec_lock:
            return await self._run_agent_with_thinking_locked(
                user_input, session_key, skill_toolboxes, skill_prompts,
                is_feishu=is_feishu, registry=registry, monitor=monitor,
                session_manager=session_manager, feishu_config=feishu_config,
                channel_router=channel_router, clawhub=clawhub,
                memory_store=memory_store, activity_log=activity_log,
                keyword_index=keyword_index, client=client,
                feishu_receive_chat_id=feishu_receive_chat_id,
                feishu_trigger_message_id=feishu_trigger_message_id,
                feishu_root_id=feishu_root_id, feishu_parent_id=feishu_parent_id,
                feishu_thread_id=feishu_thread_id,
                feishu_im_receive_id_type=feishu_im_receive_id_type,
                feishu_im_receive_id=feishu_im_receive_id,
                cli_loop_state=cli_loop_state,
                agent_config_overrides=agent_config_overrides,
                feishu_mirror_cli=feishu_mirror_cli,
            )

    async def _run_agent_with_thinking_locked(
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
        feishu_trigger_message_id: str | None = None,
        feishu_root_id: str | None = None,
        feishu_parent_id: str | None = None,
        feishu_thread_id: str | None = None,
        feishu_im_receive_id_type: str | None = None,
        feishu_im_receive_id: str | None = None,
        cli_loop_state: Any | None = None,
        agent_config_overrides: dict[str, Any] | None = None,
        feishu_mirror_cli: bool = True,
    ) -> str:
        """已获取进程级锁后的实际执行逻辑（内部方法）。

        由 run_agent_with_thinking 在 async with self._exec_lock 内调用。
        执行流程：
        1. 解析记忆依赖（memory_store/activity_log/keyword_index）
        2. 获取或创建会话上下文（SessionManager）
        3. 构建系统提示（技能 + 分层记忆摘要）
        4. 注册飞书思考回调（如启用）
        5. 注册 CLI 思考回调（如启用）
        6. 调用 run_agent 执行 Agent
        7. 更新会话历史和记忆
        8. 发送飞书回复（如启用）

        Args:
            user_input: 用户输入文本
            session_key: 会话标识（如 "default" 或 "feishu:oc_xxx"）
            skill_toolboxes: 技能工具箱列表
            skill_prompts: 技能系统提示
            is_feishu: 是否为飞书通道
            registry: 工具注册表
            monitor: 性能监控器
            session_manager: 会话管理器（必填）
            feishu_config: 飞书配置（飞书通道必填）
            channel_router: 通道路由器（飞书通道必填）
            clawhub: ClawHub 客户端（可选）
            memory_store: 记忆存储（可选）
            activity_log: 活动日志（可选）
            keyword_index: 关键词索引（可选）
            client: LLM 客户端（可选）
            feishu_receive_chat_id: 飞书接收消息的 chat_id
            feishu_trigger_message_id: 飞书触发消息 ID
            feishu_root_id: 飞书根消息 ID
            feishu_parent_id: 飞书父消息 ID
            feishu_thread_id: 飞书话题 ID
            feishu_im_receive_id_type: 飞书接收 ID 类型
            feishu_im_receive_id: 飞书接收者 ID
            cli_loop_state: CLI 循环状态
            agent_config_overrides: Agent 配置覆盖
            feishu_mirror_cli: CLI 是否镜像到飞书

        Returns:
            str: Agent 的最终回复文本

        Note:
            - 进程级执行锁防止 CLI 和飞书并发调用
            - 飞书思考使用流式卡片（PATCH 节流）
            - 同轮工具调用合并到同一卡片
        """
        ms, al, ki = resolve_memory_dependencies(memory_store, activity_log, keyword_index)

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
            session_workspace = (
                getattr(ctx, "files_path", None) or getattr(ctx, "workspace_path", None) or None
            )

        # 2. 技能与分层摘要进入 execute_plan 的 system（会话记忆由执行器 inject_memory 注入，避免重复）
        from miniagent.memory.memory_pipeline import build_layered_memory_augmentation

        layered_augment = build_layered_memory_augmentation(session_key, user_input=user_input)
        combined_skill = skill_prompts
        if layered_augment:
            combined_skill = (
                f"{skill_prompts}\n\n{layered_augment}" if skill_prompts else layered_augment
            )
        system_prompt = combined_skill.strip() if combined_skill else None

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

            from miniagent.feishu.poll_server import feishu_outbound_reply_params

            r_mid, r_thr = feishu_outbound_reply_params(feishu_trigger_message_id, feishu_thread_id)

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
                    _send_thinking,
                    append_feishu_thinking_same_card,
                    finalize_feishu_thinking_stream,
                    push_feishu_thinking_stream,
                )

                st_local = self.thinking.thinking_state(session_key)
                if st_local is None:
                    _logger.warning("思考状态无效，跳过飞书发送")
                    return
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
                    await finalize_feishu_thinking_stream(
                        feishu_config, chat_id, template, st_local
                    )
                    await _send_thinking(
                        feishu_config,
                        chat_id,
                        text,
                        template,
                        reply_to_message_id=getattr(st_local, "feishu_reply_to_message_id", None),
                        reply_in_thread=bool(getattr(st_local, "feishu_reply_in_thread", False)),
                    )

            # 如果 CLI 也绑定到此会话且策略允许镜像，注册双回调（终端 + 飞书）
            cli_dual = router.CLI_CHANNEL in bound_channels and feishu_mirror_cli
            if cli_dual:

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

                self.thinking.enable_feishu(
                    session_key,
                    im_recv,
                    _dual_send,
                    reply_to_message_id=r_mid,
                    reply_in_thread=r_thr,
                    mirror_cli=True,
                )
            else:
                self.thinking.enable_feishu(
                    session_key,
                    im_recv,
                    _feishu_send,
                    reply_to_message_id=r_mid,
                    reply_in_thread=r_thr,
                    mirror_cli=feishu_mirror_cli,
                )

        # 5. 思考回调（支持流式更新；落盘到 history 的 thinking role）
        thinking_by_label: dict[str, str] = {}
        tool_thought_lines: list[str] = []
        tool_calls_list: list[dict[str, str]] = []

        async def _thinking(
            text: str,
            streaming: bool = False,
            header: str = "",
            *,
            full_record: str | None = None,
            reset: bool = False,
            is_last_step: bool = False,
        ) -> None:
            """桥接 :meth:`run_agent` 的 ``on_thinking``：更新按标签聚合的缓冲并驱动 UI/飞书展示。

            澄清类消息（header = ``[需求澄清]``）由思考卡片统一展示，不再走直发通道。

            聚合策略（按 ``thinking_by_label`` 的 key 分组累积）：
            - 新 record 以已有内容为前缀 → **替换**（LLM 流式 chunk 增长）
            - 已有内容以新 record 为前缀 → **保留**旧内容（新 record 是旧内容的一部分）
            - 否则：**追加**到历史（澄清问题、用户回答、LLM 新 exec 轮）
            全部分隔符统一为 ``\n\n``，与 executor 内 ``_joined_phase_cumulative`` 默认一致。

            关键约束：``thinking_by_label`` 中同一 key 的内容必须与 executor 端
            ``_joined_phase_cumulative`` 输出的**纯 LLM 正文前缀一致**，否则 prefix
            检测失败导致重复。因此 ``streaming=True`` 时**不**向 thinking_by_label
            追加非 LLM 记录（工具行等），改为走 ``tool_thought_lines``。

            Args:
                reset: 若为 True，清除该 header 对应的已有聚合内容（用于避免重复显示）
                is_last_step: 若为 True，表示这是最后一步的 LLM 思考内容（不在思考区显示，避免重复）
            """
            record = full_record if full_record is not None else text
            key = header if (header or "").strip() else "__stream__"

            # reset=True 时清除已有聚合内容，避免语义不同的新阶段与旧内容拼接
            if reset:
                thinking_by_label.pop(key, None)

            if streaming and record:
                prev = thinking_by_label.get(key, "")
                if not prev:
                    thinking_by_label[key] = record
                elif record.startswith(prev):
                    # LLM 流式 chunk 前缀增长：替换为最新全文
                    thinking_by_label[key] = record
                elif prev.startswith(record):
                    # 新 record 是旧内容的一部分：保留旧内容
                    pass
                else:
                    thinking_by_label[key] = prev + "\n\n" + record
            elif record:
                # 非流式记录（工具行、澄清结果等）：统一走 tool_thought_lines，
                # 不污染 thinking_by_label 的 LLM 正文前缀
                tool_thought_lines.append(record)
            # 非流式显示使用原始 text（工具行），不用 thinking_by_label 的 LLM 正文前缀，
            # 避免 merge_tools 路径误用 LLM 内容。
            if not streaming and record:
                display_text = text
            else:
                display_text = thinking_by_label.get(key, "") if (header or "").strip() else text
            # 关键修复：传递 reset 参数，让 ThinkingDisplay 在 reset=True 时重置流式状态
            # 传递 is_last_step 参数，最后一步的 LLM 正文不在思考区显示（避免与最终结论重复）
            await self.thinking.show(
                display_text or text, session_key, streaming=streaming, header=header, reset=reset, is_last_step=is_last_step
            )

        async def _tool_finish(
            tool_name: str,
            args_json: str,
            result: str,
            success: bool,
            *,
            thinking_header: str = "",
        ) -> None:
            """工具结束回调：按环境变量决定写入历史的详略，并复用 ``_thinking`` 落盘。"""
            status = "成功" if success else "失败"
            short = f"`{tool_name}` · {status}"
            # 积累结构化数据供引擎记忆更新
            tool_calls_list.append({
                "name": tool_name,
                "args": args_json,
                "result": result,
            })
            if _tool_finish_verbose_history():
                body = (result or "").strip()
                record = (
                    f"**工具 `{tool_name}`**（{status}）\n"
                    f"- 参数：`{args_json}`\n"
                    f"- 输出：\n{_fence_tool_output(body)}"
                )
            else:
                record = short
            # 工具行用 streaming=False，走 tool_thought_lines 而非 thinking_by_label。
            # 这样可以避免工具行污染 LLM 正文前缀，导致下一轮 exec 的 prefix 检测失败。
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
            "cli_dispatch_allow_mutations": (
                True if not is_feishu else feishu_dot_commands_full_enabled()
            ),
            "feishu_receive_chat_id": _recv_chat or None,
            "feishu_trigger_message_id": (feishu_trigger_message_id or "").strip() or None,
            "feishu_root_id": (feishu_root_id or "").strip() or None,
            "feishu_parent_id": (feishu_parent_id or "").strip() or None,
            "feishu_thread_id": (feishu_thread_id or "").strip() or None,
            "feishu_im_receive_id_type": (feishu_im_receive_id_type or "").strip() or None,
            "feishu_im_receive_id": (feishu_im_receive_id or "").strip() or None,
        }
        if agent_config_overrides:
            agent_cfg_in.update(agent_config_overrides)
        from miniagent.core.config import get_default_agent_config, merge_agent_config

        merged_for_prog = merge_agent_config(get_default_agent_config(), agent_cfg_in)

        effective_registry = merged_for_prog.session_registry or registry
        if is_feishu and effective_registry is not None:
            from miniagent.feishu.agent_channel_prompts import append_feishu_channel_system

            system_prompt = append_feishu_channel_system(
                system_prompt, is_feishu=True, registry=effective_registry
            )

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
            on_plan=self._on_plan_handler(session_key),
            clawhub=clawhub,
            memory_store=ms,
            activity_log=al,
            keyword_index=ki,
            client=client,
            clarifier=self._get_clarifier(),
            session_key=session_key,
            confirmation_channel=self._get_confirmation_channel(),
            engine=self,
        )
        # 无工具调用等场景：最后一轮 LLM 流结束后无 streaming=False，需在此 PATCH 落盘全文
        if is_feishu and feishu_config:
            from miniagent.feishu.poll_server import finalize_feishu_thinking_stream

            await finalize_feishu_thinking_stream(
                feishu_config, im_recv, "gray", self.thinking.thinking_state(session_key),
            )
        # 流式思考最后一 chunk 往往不以换行结束；否则下一区块（分隔线/回复）会黏在同一行。
        self.thinking.end_thinking()

        # 7. 飞书：思考已实时发送，清理该会话的思考状态
        if is_feishu:
            self.thinking.disable_buffer(session_key)

        # 8. 更新历史（含思考过程；会话历史不总结，仅后续可归档到日记）
        def _turn_label_sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
            """将思考区块标签排序：规划步骤 → 评估 → 执行 → 第 n 轮 → 其它。"""
            lab = item[0]
            # 性能优化：使用预编译正则
            m = _STEP_NUMBER_PATTERN.search(lab)
            if m:
                return (0, int(m.group(1)), lab)
            if lab.startswith("[评估与计划]"):
                return (1, 0, lab)
            if lab.startswith("[执行]"):
                return (2, 0, lab)
            # 性能优化：使用预编译正则
            m = _ROUND_NUMBER_PATTERN.search(lab)
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
        cap = get_config("memory.history_tail_messages", 200)

        from miniagent.memory.history_progressive import run_session_history_maintenance

        # 渐进 L1–L3 后单次归档/删轮循环，避免一次调用内多轮硬切
        run_session_history_maintenance(
            session_key,
            history,
            tail_cap=cap,
            progressive_compression=merged_for_prog.history_progressive_compression,
        )

        # 9. 活动日志
        if al:
            al.log_session_start(session_key, user_input, source="feishu" if is_feishu else "cli")
            al.log_final_reply(session_key, reply)

        # 10. 持久化
        if session_manager:
            session_manager.save_session_history(session_key)

        # 11. 更新记忆存储（使用本轮实际工具调用数据）
        if ms is not None:
            try:
                from miniagent.memory.store import extract_facts, generate_turn_summary

                tool_results_text = " ".join(
                    tc.get("result", "") for tc in tool_calls_list
                )
                summary = generate_turn_summary(user_input, tool_calls_list, reply)
                facts = extract_facts(user_input + " " + reply + " " + tool_results_text)
                await ms.update_summary(session_key, summary, facts)
            except Exception as e:
                _logger.warning("Memory summary update failed: %s", e)

        try:
            from miniagent.memory.dream_scheduler import schedule_memory_maintenance

            schedule_memory_maintenance(session_key)
        except Exception as e:
            _logger.warning("Dream scheduler scheduling failed: %s", e)

        return reply

    def _on_plan_handler(self, session_key: str) -> Callable[[Any], Awaitable[bool]]:
        """创建计划确认回调。

        返回一个 async callable，通过确认侧通道暂停 agent 执行并等待用户确认。
        此回调仅在 ``plan.requires_confirmation`` 为 True 时被调用。
        """
        channel = self._get_confirmation_channel()

        async def handler(plan) -> bool:
            if channel is None:
                return True
            from miniagent.core.agent import (
                _format_plan_display_short,
                _format_plan_message,
            )
            plan_summary = _format_plan_display_short(plan, from_llm_planner=True)
            plan_full = _format_plan_message(plan, from_llm_planner=True)
            from miniagent.types.confirmation import ConfirmationRequest, ConfirmationStage

            req = ConfirmationRequest(
                stage=ConfirmationStage.PLAN,
                content=plan_summary,
                full_content=plan_full,
                context={"plan": True},
            )
            result = await channel.request_confirmation(req)
            return result.approved

        return handler

    def _get_confirmation_channel(self) -> Any:
        """获取或创建进程级确认侧通道。

        返回 ConfirmationChannel 实例，所有会话共享同一实例。
        实际的会话隔离由 channel 内部的 asyncio.Event 管理。
        """
        if self._confirmation_channel is None:
            from miniagent.core.confirmation_channel import ConfirmationChannel

            self._confirmation_channel = ConfirmationChannel()
        return self._confirmation_channel

    @property
    def confirmation_channel(self) -> Any:
        """公开访问确认通道，供 CLI 循环和飞书 handler 调用 ``respond()``。"""
        return self._get_confirmation_channel()

    def inject_message(self, session_key: str, content: str, *, session_manager: Any) -> None:
        """向指定会话注入消息。

        Args:
            session_key: 会话标识符
            content: 消息内容
            session_manager: 当前进程的会话管理器（由 ``RuntimeContext`` / 启动流程持有）
        """
        if session_manager:
            ctx = session_manager.get_or_create(session_key)
            ctx.conversation_history.append({"role": "user", "content": content, "_injected": True})

    def _get_clarifier(self) -> Any | None:
        """懒加载需求澄清器。

        受 ``MINIAGENT_REQUIREMENT_CLARIFY`` 环境变量控制（默认 ``1`` 开启）。
        首次调用时创建并缓存实例，后续调用直接返回。
        交互模式：通过确认侧通道等待用户确认/调整，而非全自动 LLM 推断。
        """
        if not get_config("features.requirement_clarify", True):
            return None
        if self._clarifier is None:
            from miniagent.core.requirement_clarifier import RequirementClarifier

            self._clarifier = RequirementClarifier(interactive=True)
        return self._clarifier


__all__ = ["UnifiedEngine"]
