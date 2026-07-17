"""Engine — AssistantTurnService 核心引擎

从原 ``unified`` 单文件拆分而来。**职责**：按 ``session_key`` 绑定 ``SessionManager`` 与会话历史；
组装技能工具箱与系统提示片段；调用对象化 :class:`miniagent.agent.Agent` 并串联 ``ThinkingDisplay``
（CLI 实时打印 / 飞书侧缓冲后卡片）；记忆服务由组合根以单一 ``MemoryRuntime`` 注入。

**非职责**：不实现飞书 WebSocket 协议细节（见 :mod:`miniagent.assistant.feishu.poll_server`）；不解析 ``.`` 命令
（见 :mod:`miniagent.assistant.engine.command_dispatch`）。与 :mod:`miniagent.agent` 的分工：core 无 asyncio 主循环与 stdin。

**并发与队列**：CLI 与飞书共用同一进程时，「执行一轮 :meth:`run_agent_with_thinking`」的调度仍应由
:class:`miniagent.assistant.infrastructure.message_queue.MessageQueueManager` 按 ``chat_id`` 串行或抢占；本类不替代队列，
仅在被调用的协程内完成单次回合的 LLM/工具编排。

详见 ``docs/ARCHITECTURE.md``（AssistantTurnService 与会话管线）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

# 性能优化：预编译高频正则表达式
_STEP_NUMBER_PATTERN = re.compile(r"\[步骤\s*(\d+)\s*/\s*(\d+)\s*\]")
_ROUND_NUMBER_PATTERN = re.compile(r"第\s*(\d+)\s*轮")

from miniagent.agent import AgentRequest, AgentRuntime, AgentSettings, AgentSpec
from miniagent.agent.logging import get_logger
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.types.confirmation import ConfirmationResult
from miniagent.assistant.engine.commands.session_management import feishu_dot_commands_full_enabled
from miniagent.assistant.infrastructure.json_config import get_config, get_config_snapshot
from miniagent.assistant.session.manager import SessionOptions

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
        - 读取 ``miniagent.agent.constants.EXECUTION_TOOL_FINISH_VERBOSE``（模块常量，默认 False）
        - 详细模式下会增加历史文件体积，用于调试或审计
    """
    from miniagent.agent.constants import EXECUTION_TOOL_FINISH_VERBOSE

    return EXECUTION_TOOL_FINISH_VERBOSE


async def _persist_session_history(session_manager: Any, session_key: str) -> None:
    """Persist history through the required asynchronous session protocol."""
    await session_manager.save_session_history_async(session_key)


def _turn_label_sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
    """将思考区块标签排序为规划、评估、执行、轮次和其它。"""
    label = item[0]
    step_match = _STEP_NUMBER_PATTERN.search(label)
    if step_match:
        return (0, int(step_match.group(1)), label)
    if label.startswith("[评估与计划]"):
        return (1, 0, label)
    if label.startswith("[执行]"):
        return (2, 0, label)
    round_match = _ROUND_NUMBER_PATTERN.search(label)
    if round_match:
        return (3, int(round_match.group(1)), label)
    return (4, 0, label)


@dataclass
class _TurnThinkingRecorder:
    """聚合单轮思考历史，并把事件转发给会话显示器。"""

    display: Any
    session_key: str
    by_label: dict[str, str] = field(default_factory=dict)
    tool_lines: list[str] = field(default_factory=list)

    async def on_thinking(
        self,
        text: str,
        streaming: bool = False,
        header: str = "",
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None:
        """合并累积式 LLM 正文，工具等非流事件另行记录。"""
        record = full_record if full_record is not None else text
        key = header if header.strip() else "__stream__"
        if reset:
            self.by_label.pop(key, None)
        if streaming and record:
            previous = self.by_label.get(key, "")
            if not previous or record.startswith(previous):
                self.by_label[key] = record
            elif not previous.startswith(record):
                self.by_label[key] = previous + "\n\n" + record
        elif record:
            self.tool_lines.append(record)
        display_text = text
        if streaming and header.strip():
            display_text = self.by_label.get(key, "")
        await self.display.show(
            display_text or text,
            self.session_key,
            streaming=streaming,
            header=header,
            reset=reset,
            is_last_step=is_last_step,
        )

    async def on_tool_finish(
        self,
        tool_name: str,
        args_json: str,
        result: str,
        success: bool,
        *,
        thinking_header: str = "",
    ) -> None:
        """把工具完成事件格式化为显示文本和可选详细历史。"""
        status = "成功" if success else "失败"
        short = f"`{tool_name}` · {status}"
        record = short
        if _tool_finish_verbose_history():
            record = (
                f"**工具 `{tool_name}`**（{status}）\n"
                f"- 参数：`{args_json}`\n"
                f"- 输出：\n{_fence_tool_output((result or '').strip())}"
            )
        await self.on_thinking(short, False, header=thinking_header, full_record=record)

    def history_blob(self) -> str:
        """按产品展示顺序生成持久化思考正文。"""
        parts = [
            f"{label}\n{blob.strip()}"
            for label, blob in sorted(self.by_label.items(), key=_turn_label_sort_key)
            if blob.strip()
        ]
        if self.tool_lines:
            parts.append("\n".join(self.tool_lines))
        return "\n\n".join(parts).strip()


@dataclass(slots=True)
class _EngineAgentObserver:
    """Translate reusable Agent events into Assistant turn state."""

    engine: Any
    session_key: str
    recorder: _TurnThinkingRecorder
    plan_handler: Callable[[Any], Awaitable[ConfirmationResult]]

    async def on_thinking(self, *args: Any, **kwargs: Any) -> None:
        await self.recorder.on_thinking(*args, **kwargs)

    def on_tool_call(self, _name: str, _arguments: str, _result: str) -> None:
        return None

    async def on_tool_finish(self, *args: Any, **kwargs: Any) -> None:
        await self.recorder.on_tool_finish(*args, **kwargs)

    async def on_plan(self, plan: Any) -> ConfirmationResult:
        return await self.plan_handler(plan)

    async def on_reflection(self, reflection: Any) -> None:
        self.engine._last_reflection[self.session_key] = reflection


def _build_turn_agent_config(
    session_key: str,
    session_workspace: str,
    history: list[dict[str, Any]],
    *,
    is_feishu: bool,
    feishu_receive_chat_id: str | None,
    feishu_trigger_message_id: str | None,
    feishu_root_id: str | None,
    feishu_parent_id: str | None,
    feishu_thread_id: str | None,
    feishu_im_receive_id_type: str | None,
    feishu_im_receive_id: str | None,
    cli_loop_state: Any | None,
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """构造引擎传给核心 Agent 的分组会话与飞书配置。"""
    receive_chat = (feishu_receive_chat_id or "").strip()
    if not receive_chat and session_key.startswith("feishu:"):
        receive_chat = session_key.removeprefix("feishu:").strip()
    config: dict[str, Any] = {
        "session_config": {
            "session_key": session_key,
            "session_workspace": session_workspace,
            "conversation_history": history,
        },
        "debug": False,
        "feishu_config": {
            "cli_loop_state": cli_loop_state,
            "cli_dispatch_allow_mutations": (
                True if not is_feishu else feishu_dot_commands_full_enabled()
            ),
            "receive_chat_id": receive_chat or None,
            "trigger_message_id": (feishu_trigger_message_id or "").strip() or None,
            "root_id": (feishu_root_id or "").strip() or None,
            "parent_id": (feishu_parent_id or "").strip() or None,
            "thread_id": (feishu_thread_id or "").strip() or None,
            "im_receive_id_type": (feishu_im_receive_id_type or "").strip() or None,
            "im_receive_id": (feishu_im_receive_id or "").strip() or None,
        },
    }
    if overrides:
        config.update(overrides)
    return config


def _build_turn_system_prompt(
    session_key: str,
    user_input: str,
    skill_prompts: str | None,
) -> str | None:
    """合并技能提示与低频分层记忆摘要，保持执行器稳定前缀。"""
    from miniagent.assistant.memory.memory_pipeline import build_layered_memory_augmentation

    layered = build_layered_memory_augmentation(session_key, user_input=user_input)
    combined = f"{skill_prompts}\n\n{layered}" if skill_prompts and layered else skill_prompts or layered
    return combined.strip() if combined else None


def _open_engine_session(
    session_manager: Any,
    session_key: str,
    *,
    is_feishu: bool,
) -> tuple[list[dict[str, Any]], str]:
    """获取会话历史与文件工作区；调用方已持有会话执行锁。"""
    options = SessionOptions(description=f"{'飞书' if is_feishu else 'CLI'}: {session_key}")
    context = session_manager.get_or_create(session_key, options)
    return context.conversation_history, session_manager.get_session_files_path(session_key)


async def _finalize_engine_turn(
    engine: Any,
    *,
    user_input: str,
    reply: str,
    session_key: str,
    history: list[dict[str, Any]],
    recorder: _TurnThinkingRecorder,
    merged_config: Any,
    memory: MemoryRuntimeProtocol,
    session_manager: Any,
    is_feishu: bool,
    feishu_config: Any,
    feishu_receive_id: str,
) -> None:
    """收尾通道流并原子更新会话历史、持久化与梦境调度。"""
    if is_feishu and feishu_config:
        from miniagent.assistant.feishu.poll_server import finalize_feishu_thinking_stream

        await finalize_feishu_thinking_stream(
            feishu_config,
            feishu_receive_id,
            "gray",
            engine.thinking.thinking_state(session_key),
            confirmation_engine=engine,
        )
    engine.thinking.end_thinking(session_key)
    if is_feishu:
        engine.thinking.disable_buffer(session_key)
    history.append({"role": "user", "content": user_input})
    thinking = recorder.history_blob()
    if thinking:
        history.append({"role": "thinking", "content": thinking})
    history.append({"role": "assistant", "content": reply})
    from miniagent.assistant.engine.bg_session_cleanup import is_background_session_key

    if is_background_session_key(session_key):
        return
    from miniagent.assistant.memory.history_progressive import run_session_history_maintenance

    run_session_history_maintenance(
        session_key,
        history,
        tail_cap=get_config("memory.history_tail_messages", 200),
        progressive_compression=merged_config.history_progressive_compression,
    )
    if session_manager:
        await _persist_session_history(session_manager, session_key)
    try:
        memory.dream_scheduler.schedule(session_key)
    except Exception as error:
        _logger.warning("Dream scheduler scheduling failed: %s", error)


@dataclass(frozen=True, slots=True)
class _AssistantTurnRequest:
    """Immutable snapshot of all product inputs needed for one assistant turn."""

    user_input: str
    session_key: str
    skill_toolboxes: tuple[Any, ...]
    skill_prompts: str | None
    memory: MemoryRuntimeProtocol
    knowledge_registry: KnowledgeRegistryProtocol
    client: Any
    is_feishu: bool = False
    registry: Any = None
    monitor: Any = None
    session_manager: Any = None
    feishu_config: Any = None
    channel_router: Any = None
    clawhub: Any | None = None
    feishu_receive_chat_id: str | None = None
    feishu_trigger_message_id: str | None = None
    feishu_root_id: str | None = None
    feishu_parent_id: str | None = None
    feishu_thread_id: str | None = None
    feishu_im_receive_id_type: str | None = None
    feishu_im_receive_id: str | None = None
    cli_loop_state: Any | None = None
    agent_config_overrides: dict[str, Any] | None = None
    feishu_mirror_cli: bool = True


class AssistantTurnService:
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
        """初始化引擎：创建思考显示器、懒加载澄清器、会话级执行协调器。"""
        from miniagent.assistant.engine.session_exec import SessionExecCoordinator
        from miniagent.assistant.engine.thinking import ThinkingDisplay

        self.thinking = ThinkingDisplay()
        self._clarifier: Any | None = None
        self._session_exec = SessionExecCoordinator()
        self._confirmation_channels: dict[str, Any] = {}
        self._last_reflection: dict[str, Any] = {}
        self._active_session_key: str | None = None
        from miniagent.agent.constants import EXECUTION_MAX_CONCURRENT_TOOLS

        self._tool_semaphore = asyncio.Semaphore(max(1, min(20, EXECUTION_MAX_CONCURRENT_TOOLS)))

    async def run_agent_with_thinking(
        self,
        user_input: str,
        session_key: str,
        skill_toolboxes: list,
        skill_prompts: str | None,
        *,
        memory: MemoryRuntimeProtocol,
        knowledge_registry: KnowledgeRegistryProtocol,
        client: Any,
        is_feishu: bool = False,
        registry: Any = None,
        monitor: Any = None,
        session_manager: Any = None,
        feishu_config: Any = None,
        channel_router: Any = None,
        clawhub: Any | None = None,
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
        _hold_session_lock: bool = False,
    ) -> str:
        """运行 agent 并显示思考过程。

        CLI: 终端实时显示
        飞书: 缓冲思考步骤，完成后发送

            memory: 由应用组合根注入的完整记忆运行时
            knowledge_registry: 由应用组合根注入的知识库注册表
            is_feishu: 当前请求是否来自飞书通道（非独立启动形态；进程始终带 CLI）
            registry: 工具注册表（注入）
            monitor: 性能监控器（注入）
            session_manager: 会话管理器（**必填**；负责 ``get_or_create``、历史持久化与会话 ``files`` 路径）
            feishu_config: 飞书配置（注入）
            channel_router: 通道路由器（飞书思考多通道回调时使用）
            clawhub: ClawHub 客户端（注入至工具上下文，技能搜索/安装复用）
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
            feishu_mirror_cli: 飞书会话绑定 CLI 通道时，是否将思考/输出镜像到终端（默认 True）。
            _hold_session_lock: 调用方已通过 :meth:`session_turn` 持有会话锁时为 True，跳过二次
                ``acquire``，避免 ``asyncio.Lock`` 不可重入死锁。
        """
        if session_manager is None:
            raise ValueError(
                "run_agent_with_thinking 需要注入 session_manager（会话历史与工作区依赖 SessionManager）"
            )
        request = _AssistantTurnRequest(
            user_input=user_input,
            session_key=session_key,
            skill_toolboxes=tuple(skill_toolboxes),
            skill_prompts=skill_prompts,
            memory=memory,
            knowledge_registry=knowledge_registry,
            client=client,
            is_feishu=is_feishu,
            registry=registry,
            monitor=monitor,
            session_manager=session_manager,
            feishu_config=feishu_config,
            channel_router=channel_router,
            clawhub=clawhub,
            feishu_receive_chat_id=feishu_receive_chat_id,
            feishu_trigger_message_id=feishu_trigger_message_id,
            feishu_root_id=feishu_root_id,
            feishu_parent_id=feishu_parent_id,
            feishu_thread_id=feishu_thread_id,
            feishu_im_receive_id_type=feishu_im_receive_id_type,
            feishu_im_receive_id=feishu_im_receive_id,
            cli_loop_state=cli_loop_state,
            agent_config_overrides=(
                dict(agent_config_overrides) if agent_config_overrides is not None else None
            ),
            feishu_mirror_cli=feishu_mirror_cli,
        )
        if _hold_session_lock:
            return await self._run_agent_with_thinking_locked(request)

        async with self._session_exec.acquire(session_key):
            return await self._run_agent_with_thinking_locked(request)

    @asynccontextmanager
    async def session_turn(self, session_key: str) -> AsyncIterator[None]:
        """以 ``session_key`` 为粒度持有会话执行锁，覆盖一整轮 turn。

        用于把「打印 You 问题块 → 执行 Agent → 打印答案块」整段纳入同一串行边界，
        使同一会话的 CLI 与飞书 turn 严格排队、原子呈现（不交错、不重复驱动），
        不同 ``session_key`` 仍可并行（受 ``max_parallel_sessions`` 限流）。

        锁内调用 :meth:`run_agent_with_thinking` 时务必传 ``_hold_session_lock=True``，
        否则会二次 acquire 同一 asyncio.Lock 而死锁。
        """
        async with self._session_exec.acquire(session_key):
            yield

    async def _enable_feishu_thinking(
        self,
        session_key: str,
        feishu_config: Any,
        channel_router: Any,
        receive_chat_id: str | None,
        trigger_message_id: str | None,
        thread_id: str | None,
        mirror_cli: bool,
    ) -> str:
        """为单轮飞书请求注册思考卡片投递回调并返回接收 ID。"""
        if channel_router is None:
            raise ValueError("channel_router 为必填（飞书会话且提供 feishu_config 时）")
        bound_channels = channel_router.get_bound_channels(session_key)
        receive_id = (receive_chat_id or "").strip()
        if not receive_id and session_key.startswith("feishu:"):
            receive_id = session_key[len("feishu:") :]
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        reply_message_id, reply_in_thread = feishu_outbound_reply_params(
            trigger_message_id, thread_id
        )

        async def send(
            chat_id: str,
            text: str,
            template: str,
            *,
            is_new_round: bool = False,
            streaming: bool = True,
            merge_tools: bool = False,
            finalize_only: bool = False,
        ) -> None:
            from miniagent.assistant.feishu.poll_server import (
                _send_thinking,
                append_feishu_thinking_same_card,
                finalize_feishu_thinking_stream,
                push_feishu_thinking_stream,
            )

            state = self.thinking.thinking_state(session_key)
            if state is None:
                _logger.warning("思考状态无效，跳过飞书发送")
                return
            if finalize_only:
                await finalize_feishu_thinking_stream(
                    feishu_config, chat_id, template, state, confirmation_engine=self
                )
            elif streaming:
                await push_feishu_thinking_stream(
                    feishu_config,
                    chat_id,
                    text,
                    template,
                    state,
                    new_round=is_new_round,
                    confirmation_engine=self,
                )
            elif merge_tools:
                await append_feishu_thinking_same_card(
                    feishu_config, chat_id, text, template, state, confirmation_engine=self
                )
            else:
                await finalize_feishu_thinking_stream(
                    feishu_config, chat_id, template, state, confirmation_engine=self
                )
                await _send_thinking(
                    feishu_config,
                    chat_id,
                    text,
                    template,
                    reply_to_message_id=getattr(state, "feishu_reply_to_message_id", None),
                    reply_in_thread=bool(getattr(state, "feishu_reply_in_thread", False)),
                )

        cli_dual = channel_router.CLI_CHANNEL in bound_channels and mirror_cli
        self.thinking.enable_feishu(
            session_key,
            receive_id,
            send,
            reply_to_message_id=reply_message_id,
            reply_in_thread=reply_in_thread,
            mirror_cli=True if cli_dual else mirror_cli,
        )
        return receive_id

    async def _run_agent_with_thinking_locked(
        self,
        request: _AssistantTurnRequest,
    ) -> str:
        """在已持有会话执行锁时运行一轮 Agent，并完成通道与历史收尾。"""
        history, session_workspace = _open_engine_session(
            request.session_manager,
            request.session_key,
            is_feishu=request.is_feishu,
        )
        system_prompt = _build_turn_system_prompt(
            request.session_key,
            request.user_input,
            request.skill_prompts,
        )
        self.thinking.reset_counter(request.session_key)
        receive_id = (request.feishu_receive_chat_id or "").strip()
        if request.is_feishu and request.feishu_config:
            receive_id = await self._enable_feishu_thinking(
                request.session_key,
                request.feishu_config,
                request.channel_router,
                request.feishu_receive_chat_id,
                request.feishu_trigger_message_id,
                request.feishu_thread_id,
                request.feishu_mirror_cli,
            )
        thinking_recorder = _TurnThinkingRecorder(self.thinking, request.session_key)
        agent_cfg_in = _build_turn_agent_config(
            request.session_key,
            session_workspace,
            history,
            is_feishu=request.is_feishu,
            feishu_receive_chat_id=request.feishu_receive_chat_id,
            feishu_trigger_message_id=request.feishu_trigger_message_id,
            feishu_root_id=request.feishu_root_id,
            feishu_parent_id=request.feishu_parent_id,
            feishu_thread_id=request.feishu_thread_id,
            feishu_im_receive_id_type=request.feishu_im_receive_id_type,
            feishu_im_receive_id=request.feishu_im_receive_id,
            cli_loop_state=request.cli_loop_state,
            overrides=request.agent_config_overrides,
        )
        from miniagent.agent.config import get_default_agent_config, merge_agent_config

        merged_for_prog = merge_agent_config(get_default_agent_config(), agent_cfg_in)
        effective_registry = merged_for_prog.session_config.session_registry or request.registry
        if request.is_feishu and effective_registry is not None:
            from miniagent.assistant.feishu.agent_channel_prompts import (
                append_feishu_channel_system,
            )

            system_prompt = append_feishu_channel_system(
                system_prompt, is_feishu=True, registry=effective_registry
            )
        reply = await self._invoke_core_agent(
            request,
            agent_config=agent_cfg_in,
            system_prompt=system_prompt,
            recorder=thinking_recorder,
        )
        await _finalize_engine_turn(
            self,
            user_input=request.user_input,
            reply=reply,
            session_key=request.session_key,
            history=history,
            recorder=thinking_recorder,
            merged_config=merged_for_prog,
            memory=request.memory,
            session_manager=request.session_manager,
            is_feishu=request.is_feishu,
            feishu_config=request.feishu_config,
            feishu_receive_id=receive_id,
        )
        return reply

    async def _invoke_core_agent(
        self,
        request: _AssistantTurnRequest,
        *,
        agent_config: dict[str, Any],
        system_prompt: str | None,
        recorder: _TurnThinkingRecorder,
    ) -> str:
        """调用纯核心 Agent，并注入当前会话的确认与思考回调。"""
        _logger.debug(
            "run_agent 调度: session_key=%s source=%s input=%.40s",
            request.session_key,
            "feishu" if request.is_feishu else "cli",
            (request.user_input or "").replace("\n", " "),
        )
        observer = _EngineAgentObserver(
            self,
            request.session_key,
            recorder,
            self._on_plan_handler(request.session_key),
        )
        runtime = AgentRuntime(AgentSpec(
            settings=AgentSettings(get_config_snapshot()),
            registry=request.registry,
            memory=request.memory,
            knowledge=request.knowledge_registry,
            monitor=request.monitor,
            observer=observer,
            clawhub=request.clawhub,
            clarifier=self._get_clarifier(),
            confirmation_channel=self._get_confirmation_channel(request.session_key),
            tool_semaphore=self._tool_semaphore,
            owns_llm=False,
            owns_memory=False,
        ), request.client)
        await runtime.start()
        try:
            agent_result = await runtime.run(
                AgentRequest(
                    user_input=request.user_input,
                    session_key=request.session_key,
                    toolboxes=request.skill_toolboxes,
                    system_prompt=system_prompt,
                    config=agent_config,
                ),
            )
            return agent_result.reply
        finally:
            await runtime.stop()

    def _on_plan_handler(self, session_key: str) -> Callable[[Any], Awaitable[ConfirmationResult]]:
        """创建计划确认回调。

        返回一个 async callable，通过确认侧通道暂停 agent 执行并等待用户确认。
        此回调仅在 ``plan.requires_confirmation`` 为 True 时被调用。
        """
        channel = self._get_confirmation_channel(session_key)

        async def handler(plan) -> ConfirmationResult:
            from miniagent.agent.agent import (
                _format_plan_display_short,
                _format_plan_message,
            )
            from miniagent.agent.types.confirmation import ConfirmationRequest, ConfirmationStage

            plan_summary = _format_plan_display_short(plan, from_llm_planner=True)
            plan_full = _format_plan_message(plan, from_llm_planner=True)
            req = ConfirmationRequest(
                stage=ConfirmationStage.PLAN,
                content=plan_summary,
                full_content=plan_full,
                context={
                    "plan_summary": getattr(plan, "summary", "") or "",
                    "risk_level": getattr(plan, "risk_level", None) or "",
                    "requires_confirmation": bool(getattr(plan, "requires_confirmation", False)),
                },
            )
            return await channel.request_confirmation(req)

        return handler

    def _get_confirmation_channel(self, session_key: str | None = None) -> Any:
        """获取或创建指定会话的确认侧通道（每 session_key 独立实例）。"""
        key = (session_key or self._active_session_key or "default").strip() or "default"
        channel = self._confirmation_channels.get(key)
        if channel is None:
            from miniagent.agent.confirmation_channel import ConfirmationChannel

            channel = ConfirmationChannel()
            self._confirmation_channels[key] = channel
        return channel

    def get_confirmation_channel(self, session_key: str) -> Any:
        """公开访问指定会话的确认通道。"""
        return self._get_confirmation_channel(session_key)

    def set_active_session_key(self, session_key: str | None) -> None:
        """CLI 主循环切换活跃会话时更新，供 ``confirmation_channel`` 属性路由。"""
        self._active_session_key = session_key

    @property
    def confirmation_channel(self) -> Any:
        """当前活跃会话的确认通道（CLI 路径）；飞书入站应使用 ``get_confirmation_channel``。"""
        return self._get_confirmation_channel(self._active_session_key)

    def get_last_reflection(self, session_key: str) -> Any | None:
        """获取指定会话最近一次反思评估结果（由 ``run_agent`` Phase 3 写入）。

        反思正文已并入 assistant 回复 footer 供展示；本缓存供外部按需读取。
        飞书 handler 在发送结论卡片后通常调用 :meth:`clear_last_reflection` 清理。
        """
        return self._last_reflection.get(session_key)

    def clear_last_reflection(self, session_key: str) -> None:
        """清除指定会话的反思评估缓存。"""
        self._last_reflection.pop(session_key, None)

    def get_thinking_display(self) -> Any:
        """返回思考显示器实例（:class:`AssistantTurnServiceProtocol`）。"""
        return self.thinking

    def inject_message(self, session_key: str, content: str, *, session_manager: Any) -> None:
        """向指定会话的内存历史注入一条用户消息。

        仅 append 到 ``conversation_history``，**不**调用 ``save_session_history``；
        调用方需自行持久化。消息带 ``_injected: True`` 标记以区别于真实用户输入。

        Args:
            session_key: 会话标识符
            content: 消息内容
            session_manager: 当前进程的会话管理器（由 ``ApplicationContainer`` / 启动流程持有；
                为 ``None`` 时静默跳过）
        """
        if session_manager:
            ctx = session_manager.get_or_create(session_key)
            ctx.conversation_history.append({"role": "user", "content": content, "_injected": True})

    def _get_clarifier(self) -> Any | None:
        """懒加载需求澄清器。

        受 ``get_config("features.requirement_clarify", True)`` 控制（见 ``config.defaults.json``）。
        首次调用时创建并缓存实例，后续调用直接返回。
        交互模式：通过确认侧通道等待用户确认/调整，而非全自动 LLM 推断。
        """
        if not get_config("features.requirement_clarify", True):
            return None
        if self._clarifier is None:
            from miniagent.agent.requirement_clarifier import RequirementClarifier

            self._clarifier = RequirementClarifier(interactive=True)
        return self._clarifier


__all__ = ["AssistantTurnService"]
