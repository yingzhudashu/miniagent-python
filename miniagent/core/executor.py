"""Mini Agent Python — ReAct 循环执行器（两阶段中的执行阶段）

执行 Phase 1 产出的结构化计划，实现 ReAct 循环（Think → Act → Observe）。

工作流程：
1. 根据 plan.requiredToolboxes 筛选工具
2. 初始化循环检测器 / 上下文管理器
3. 注入三层记忆
4. ReAct 循环：LLM 调用 → 工具执行 → 结果反馈
5. 循环直到：LLM 不再调用工具 / 达到 maxTurns / 循环检测拦截

``MINIAGENT_PHASED_EXECUTION`` 开启且 ``plan.steps`` 非空时，按步骤分子循环（每步独立 thinking 解析）；
若最后一步单步子轮次用尽而全局 ``MINIAGENT_AGENT_MAX_TURNS`` 仍有余量，会追加一轮不传 tools 的收尾 synthesis。
详见环境变量说明与 ``docs/ARCHITECTURE.md``。

**工具结果回注**：每轮工具输出经 ``tool`` role 消息写回 ``DefaultContextManager``；同轮 ``merge_tools``（若配置开启）可在展示层合并多工具行，但**不影响**此处消息序列语义。

**不变量**：工具调用均在 :class:`miniagent.types.tool.ToolContext` 限定的 ``cwd`` / ``allowed_paths`` 内执行
（通常由沙箱默认工作区推导）。上下文 token 超预算时抛出
:class:`miniagent.memory.context.ContextBudgetExceeded`，由上层决定是否换会话或压缩。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from collections.abc import Callable
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from openai import AsyncOpenAI

from miniagent.core.config import get_default_agent_config, get_default_model_config
from miniagent.core.llm_params import resolve_exec_completion_kwargs
from miniagent.core.openai_client import get_shared_async_openai
from miniagent.core.openai_message_sanitize import strip_leading_underscore_keys_from_messages
from miniagent.core.prompts.identity import AGENT_IDENTITY
from miniagent.core.thinking_callback import invoke_on_thinking
from miniagent.core.thinking_presets import map_business_depth
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import append_log, get_logger, truncate
from miniagent.infrastructure.loop_detector import LoopDetector
from miniagent.infrastructure.timezone_config import format_agent_timezone_context
from miniagent.infrastructure.tracing import emit_trace
from miniagent.knowledge import get_kb_registry
from miniagent.memory.context import ContextBudgetExceeded, DefaultContextManager
from miniagent.memory.defaults import resolve_memory_dependencies
from miniagent.memory.embedding_search import (
    embedding_search_enabled,
    get_embed_provider,
)
from miniagent.memory.history_bridge import conversation_history_for_llm
from miniagent.memory.keyword_index import format_search_results, search_relevant_with_index
from miniagent.memory.store import extract_facts, generate_turn_summary
from miniagent.security.sandbox import get_default_workspace
from miniagent.types.agent import LoopDetectionConfig, ToolMonitorProtocol
from miniagent.types.config import AgentConfig
from miniagent.types.error_prefix import WARNING_PREFIX
from miniagent.types.memory import MemoryEntryInput, MemoryStoreProtocol
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.protocols import (
    ActivityLogProtocol,
    KeywordIndexProtocol,
    OnThinkingCallback,
    OnToolFinishCallback,
)
from miniagent.types.skill import ClawHubClientProtocol
from miniagent.types.tool import ToolContext, ToolRegistryProtocol, ToolResult

# ── 性能优化：工具意图映射改为模块级常量，避免每次调用重建 ──
_TOOL_INTENT_MAP: dict[str, str] = {
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    "list_dir": "列出目录",
    "exec_command": "执行命令",
    "web_search": "搜索网页",
    "browser_extract_text": "浏览器提取正文",
    "fetch_url": "抓取网页",
    "read_memory": "读取记忆",
    "write_memory": "写入记忆",
    "search_memory": "搜索记忆",
    "git_status": "Git 状态",
    "git_diff": "Git 差异",
}

# ── 性能优化：工具并发限制（避免资源耗尽）──
_tool_semaphore: asyncio.Semaphore | None = None

def _get_tool_semaphore() -> asyncio.Semaphore:
    """获取全局工具并发限制信号量（惰性初始化）。

    性能优化：
    - 控制并发工具数，避免API限流
    - 防止资源耗尽（内存、CPU、网络）
    - 可配置并发上限

    Returns:
        asyncio.Semaphore 实例
    """
    global _tool_semaphore
    if _tool_semaphore is None:
        max_concurrent = max(1, min(20, int(get_config("execution.max_concurrent_tools", 5))))
        _tool_semaphore = asyncio.Semaphore(max_concurrent)
        _logger.debug("工具并发限制已初始化: %d", max_concurrent)
    return _tool_semaphore

_logger = get_logger(__name__)

# ─── 工具错误日志辅助 ────────────────────────────────────────────

# 参数截断长度（避免日志膨胀）- 支持环境变量覆盖
import os as _os_for_log

_MAX_ARGS_LOG_LEN = int(_os_for_log.environ.get("MINIAGENT_MAX_ARGS_LOG_LEN", "500"))


def _truncate_args_for_log(args: dict[str, Any] | str, max_len: int = _MAX_ARGS_LOG_LEN) -> str:
    """截断工具参数用于日志输出，避免大内容导致日志膨胀。

    Args:
        args: 工具参数字典或 JSON 字符串
        max_len: 最大长度（字符）

    Returns:
        截断后的字符串
    """
    if isinstance(args, str):
        if len(args) <= max_len:
            return args
        return args[:max_len] + "...[截断]"
    try:
        result = json.dumps(args, ensure_ascii=False)
        if len(result) <= max_len:
            return result
        return result[:max_len] + "...[截断]"
    except Exception:
        return str(args)[:max_len]


def _log_tool_error(
    *,
    tool_name: str,
    tool_call_id: str | None,
    args: dict[str, Any],
    session_key: str | None,
    error_type: str,
    error_message: str,
    is_user_error: bool = False,
    traceback_str: str | None = None,
) -> None:
    """统一记录工具错误日志，区分用户误用与工具缺陷。"""
    args_str = _truncate_args_for_log(args)
    log_prefix = f"[工具错误] {tool_name}"
    emit_trace({
        "type": "tool.error",
        "tool": tool_name,
        "tool_call_id": tool_call_id,
        "args_truncated": args_str,
        "session_key": session_key,
        "error_type": error_type,
        "error_message": error_message,
        "is_user_error": is_user_error,
    })
    if is_user_error:
        _logger.warning(
            "%s | 类型: %s | 参数: %s | 消息: %s | 会话: %s",
            log_prefix, error_type, args_str, error_message, session_key or "N/A",
        )
    else:
        _logger.error(
            "%s | 类型: %s | 参数: %s | 消息: %s | 会话: %s",
            log_prefix, error_type, args_str, error_message, session_key or "N/A",
        )
        if traceback_str:
            _logger.debug("%s | 堆栈:\n%s", log_prefix, traceback_str)


# ─── 流式输出优化：增量 buffer（性能优化）──


class StreamingBuffer:
    """高效的流式内容缓冲器，避免 O(n²) 字符拼接。

    性能优化：降低合并阈值从100到50，减少内存占用和getvalue复杂度。

    设计原理：
    - 维护一个增长的 chunk 列表
    - 当列表过长时（>50），合并为单个字符串（降低阈值）
    - 提供 getvalue() 获取当前内容

    Example:
        >>> buffer = StreamingBuffer()
        >>> for chunk in stream:
        >>>     buffer.append(chunk)
        >>> content = buffer.getvalue()
    """

    __slots__ = ("_chunks", "_length", "_consolidated")

    def __init__(self) -> None:
        """初始化空缓冲器。"""
        self._chunks: list[str] = []
        self._length: int = 0
        self._consolidated: str | None = None

    def append(self, chunk: str) -> None:
        """追加一个 chunk。

        Args:
            chunk: 要追加的文本块
        """
        self._chunks.append(chunk)
        self._length += len(chunk)
        # 性能优化：降低合并阈值从100到50，减少内存占用
        if len(self._chunks) > 50:
            self._consolidated = "".join(self._chunks)
            self._chunks = [self._consolidated]

    def getvalue(self) -> str:
        """性能优化：简化逻辑，快速返回当前内容。

        Returns:
            缓冲器中的完整文本内容
        """
        if self._consolidated is not None:
            # 已合并过，直接返回或追加新 chunks
            if len(self._chunks) == 1:
                return self._consolidated
            # 合并后又有新追加（简化拼接）
            return self._consolidated + "".join(self._chunks[1:])
        # 未合并过，直接拼接
        return "".join(self._chunks)

    def __len__(self) -> int:
        """返回当前内容长度。

        Returns:
            缓冲内容的字符数
        """
        return self._length

    def clear(self) -> None:
        """清空缓冲器。"""
        self._chunks.clear()
        self._length = 0
        self._consolidated = None


# ─── Agent 身份（从 prompts 模块导入）────────────────────────────

# AGENT_IDENTITY 现在从 miniagent.core.prompts.identity 导入
# 使用 XML 标签结构化，遵循 Claude 最佳实践


def build_execution_system_prompt(
    *,
    agent_identity: str,
    caller_system_prompt: str | None,
    plan_summary: str,
    keyword_context: str | None,
    kb_context: str | None = None,
    session_files_root: str | None = None,
) -> str:
    """按约定拼接执行阶段 system prompt。

    按顺序拼接：身份 → 调用方技能/指令 → 任务摘要 → 关键词检索上下文 → 知识库上下文 → 文件根目录 → 时区。

    Args:
        agent_identity: Agent 身份描述（如 "你是 MiniAgent..."）
        caller_system_prompt: 调用方传入的系统指令（如技能合并文案）
        plan_summary: 当前任务的执行计划摘要
        keyword_context: 关键词检索返回的相关记忆上下文
        kb_context: 知识库检索返回的相关文档上下文
        session_files_root: 会话文件根目录（用于工具路径解析）

    Returns:
        str: 拼接后的完整 system prompt

    Note:
        - 各部分之间用双换行分隔
        - 空内容会被跳过
        - 自动注入当前时区信息
    """
    parts: list[str] = [agent_identity.strip()]
    if caller_system_prompt and caller_system_prompt.strip():
        parts.append(caller_system_prompt.strip())
    parts.append(f"当前任务：{plan_summary.strip()}")
    if keyword_context and keyword_context.strip():
        parts.append(keyword_context.strip())
    if kb_context and kb_context.strip():
        parts.append(kb_context.strip())
    root = (session_files_root or "").strip()
    if root:
        abs_root = os.path.abspath(root)
        parts.append(
            "本回合默认文件根目录："
            f"{abs_root}。read_file、write_file、list_dir、edit_file 等工具的路径参数若为相对路径，"
            "均相对于该目录；不要使用 `../` 等方式逃逸到该目录之外。"
            "\n\n## 会话文件\n"
            "用户在对话中上传过文件到 feishu_incoming/ 子目录。"
            "如需参考文件内容，可使用 read_file 等工具读取。"
        )
    parts.append(format_agent_timezone_context())
    return "\n\n".join(parts)


def get_client() -> AsyncOpenAI:
    """获取进程内共享 AsyncOpenAI（与 :func:`get_shared_async_openai` 相同）。"""
    return get_shared_async_openai()


# ─── 环境变量缓存（性能优化）────────────────────────────────────

@lru_cache(maxsize=1)
def _env_phased_execution_enabled() -> bool:
    """是否启用分阶段执行（工具批次与 LLM 轮次分段），默认开启。"""
    return get_config("execution.phased_enabled", True)


@lru_cache(maxsize=1)
def _tool_intent_in_thinking_enabled() -> bool:
    """是否在工具执行前向 on_thinking 推送 🔧 意图行。"""
    return get_config("execution.tool_intent_in_thinking", False)


@lru_cache(maxsize=1)
def _step_max_turns_cap() -> int:
    """分步模式下单步内 ReAct 轮数上限（默认 48）。"""
    return get_config("execution.step_max_turns", 48)


@lru_cache(maxsize=1)
def _thinking_segment_separator() -> str:
    """同一步内多轮 LLM 思考片段拼接符；默认双换行。"""
    raw = get_config("execution.thinking_separator", "")
    if raw:
        return raw.replace("\\n", "\n")
    return "\n\n"


@lru_cache(maxsize=1)
def _tool_intent_max_chars() -> int:
    """工具意图摘要写入思考流时的最大字符数。"""
    return get_config("execution.tool_intent_max_chars", 4000)


def _reset_env_caches_for_tests() -> None:
    """重置环境变量缓存（仅供测试使用）。"""
    _env_phased_execution_enabled.cache_clear()
    _tool_intent_in_thinking_enabled.cache_clear()
    _step_max_turns_cap.cache_clear()
    _thinking_segment_separator.cache_clear()
    _tool_intent_max_chars.cache_clear()


def _resolve_exec_tools(
    effective_registry: ToolRegistryProtocol,
    agent_config: AgentConfig,
    plan: StructuredPlan,
    step: PlanStep | None,
) -> list[Any]:
    """与主流程一致的工具筛选；``step`` 非空且含 required_toolboxes 时按步骤覆盖。"""
    step_tbs = list(step.required_toolboxes) if step and step.required_toolboxes else None
    plan_tbs = plan.required_toolboxes

    if agent_config.tool_selection_strategy == "all":
        return effective_registry.get_schemas()
    if agent_config.tool_selection_strategy == "auto":
        tbs = step_tbs if step_tbs else plan_tbs
        if tbs:
            return effective_registry.get_schemas_by_toolboxes(tbs)
        tools = [t.schema for t in effective_registry.get_all().values() if t.toolbox is None]
        return tools if tools else effective_registry.get_schemas()
    tbs = step_tbs if step_tbs else plan_tbs
    return effective_registry.get_schemas_by_toolboxes(tbs)


def _step_thinking_header(si: int, n_steps: int, step: PlanStep) -> str:
    """分步执行时用于思考展示/合并的步骤级 header。"""
    sn = int(step.step_number) if step.step_number is not None else si + 1
    desc = (step.description or "").strip().replace("\n", " ")
    if len(desc) > 72:
        desc = desc[:69] + "…"
    return f"[步骤 {sn}/{n_steps}] {desc}".strip()


def _append_context_or_return(
    context_manager: DefaultContextManager,
    msg: dict[str, Any],
) -> str | None:
    """追加消息；若 overflow_strategy=error 且超预算则返回错误文案。"""
    try:
        context_manager.append(msg)
    except ContextBudgetExceeded as e:
        return f"{WARNING_PREFIX} {e}"
    return None


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall = Callable[[str, str, str], None]  # (name, args_json, result)
# 使用 Protocol 类型替代 Callable[..., Any]，详见 miniagent/types/protocols.py
OnThinking = OnThinkingCallback  # (text, streaming, header, *, full_record=..., reset=...)
OnToolFinish = OnToolFinishCallback  # (name, args_json, result, success, *, thinking_header=...)


# ─── 核心：execute_plan（ReAct 主循环；可选分步子循环 + 无 tools 收尾 synthesis）──


async def execute_plan(
    plan: StructuredPlan,
    user_input: str,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol,
    agent_config: AgentConfig,
    on_tool_call: OnToolCall | None = None,
    on_thinking: OnThinking | None = None,
    *,
    on_tool_finish: OnToolFinish | None = None,
    system_prompt: str | None = None,
    clawhub: ClawHubClientProtocol | None = None,
    memory_store: MemoryStoreProtocol | None = None,
    activity_log: ActivityLogProtocol | None = None,
    keyword_index: KeywordIndexProtocol | None = None,
    client: AsyncOpenAI | None = None,
) -> str:
    """执行结构化计划（ReAct 循环）。

    Args:
        plan: 来自 Phase 1 的结构化执行计划
        user_input: 用户原始需求
        registry: 工具注册表
        monitor: 性能监控器
        agent_config: 合并后的 Agent 配置
        on_tool_call: 工具调用回调（如未知工具等路径）
        on_tool_finish: 每个工具执行完成后异步回调（名称、参数 JSON 字符串、完整结果、是否成功）。
            若回调签名包含关键字参数 ``thinking_header``（或 ``**kwargs``），将传入当前 ReAct 轮标签（如 ``[第 1 轮]``）；否则仅按四参调用。
        memory_store: 记忆存储（默认与 ``MINIAGENT_PATHS_STATE_DIR`` 进程 bundle 一致）
        activity_log: 活动日志（同上）
        keyword_index: 关键词索引（同上；缺省时优先使用 store 已绑定索引）
        client: LLM 客户端（默认进程内共享 AsyncOpenAI）
        system_prompt: 调用方注入的系统指令（如技能合并文案）；与身份、任务摘要等按序合并

    Returns:
        LLM 的最终回复文本
    """
    ms, al, ki = resolve_memory_dependencies(memory_store, activity_log, keyword_index)

    # ── 工具筛选 ──
    effective_registry = agent_config.session_registry or registry
    tools = _resolve_exec_tools(effective_registry, agent_config, plan, None)

    # ── 执行上下文 ──
    workspace = agent_config.session_workspace or get_default_workspace()
    # 允许路径：workspace 作为主工作目录（会话 files/ 或 cwd），
    # 同时加入项目根目录以确保 exec/mkdir 等环境操作不受路径限制。
    _cwd = os.getcwd()
    _allowed = list(dict.fromkeys([workspace, _cwd]))  # 去重但保持顺序
    mq_abort = (agent_config.feishu_receive_chat_id or "").strip() or None
    rid_raw = (getattr(agent_config, "feishu_im_receive_id_type", None) or "").strip().lower()
    if rid_raw not in ("chat_id", "open_id", "union_id"):
        # 从JSON配置获取（支持环境变量覆盖）
        rid_raw = get_config("feishu.receive_id_type", "chat_id")
    feishu_rid_type = rid_raw if rid_raw in ("chat_id", "open_id", "union_id") else None
    im_recv_alt = (getattr(agent_config, "feishu_im_receive_id", None) or "").strip() or None
    ctx = ToolContext(
        cwd=workspace,
        allowed_paths=_allowed,
        permission="allowlist",
        clawhub=clawhub,
        session_key=agent_config.session_key,
        cli_loop_state=agent_config.cli_loop_state,
        cli_dispatch_allow_mutations=agent_config.cli_dispatch_allow_mutations,
        message_queue_abort_chat_id=mq_abort,
        feishu_im_receive_id_type=feishu_rid_type,
        feishu_im_receive_id=im_recv_alt,
    )

    # ── 循环检测器 ──
    # 如果agent_config.loop_detection为空，使用默认配置
    loop_config_data = agent_config.loop_detection or get_default_agent_config().loop_detection
    loop_config = (
        LoopDetectionConfig(**loop_config_data)
        if isinstance(loop_config_data, dict)
        else loop_config_data
    )
    loop_detector = LoopDetector(loop_config)

    # ── 上下文管理器 ──
    model_config = get_default_model_config()
    context_manager = DefaultContextManager(
        context_window=model_config.context_window,
        compress_threshold=agent_config.context_compress_threshold,
        tools=tools,
        overflow_strategy=agent_config.context_overflow_strategy,
    )

    # ── System prompt + 记忆注入 ──
    keyword_context: str | None = None
    if agent_config.session_key:
        memory = await ms.load(agent_config.session_key)

        # ── 性能优化：并行执行嵌入搜索和关键词索引 ──
        relevant: list[dict[str, Any]] = []

        # 并行执行嵌入搜索和关键词索引
        embed_task = None
        kw_task = None

        if embedding_search_enabled():
            try:
                provider = get_embed_provider(
                    state_dir=ms._state_dir if hasattr(ms, "_state_dir") else "workspaces"
                )
                embed_task = asyncio.create_task(
                    provider.search(user_input, limit=8, min_score=0.3)
                )
            except Exception as e:
                _logger.debug("嵌入搜索初始化失败 (%s)", e)

        # 关键词索引并行搜索
        kw_task = asyncio.create_task(
            asyncio.to_thread(
                search_relevant_with_index, ki, user_input, 8, 0
            )
        )

        # 并行等待结果
        embed_results = None
        kw_results = []

        if embed_task and kw_task:
            results = await asyncio.gather(
                embed_task, kw_task, return_exceptions=True
            )
            if not isinstance(results[0], Exception):
                embed_results = results[0]
            if not isinstance(results[1], Exception):
                kw_results = results[1]
        elif kw_task:
            kw_results = await kw_task

        # 合并结果（嵌入搜索优先）
        seen: set[tuple[str, str]] = set()
        if embed_results:
            relevant = provider.expand_results(embed_results)
            for r in relevant:
                seen.add((r["session_id"], r["timestamp"]))
            if agent_config.debug:
                _logger.debug("嵌入搜索: %d 条相关记忆", len(relevant))

        # 关键词索引补充
        for kw in kw_results:
            key = (kw["session_id"], kw["timestamp"])
            if key not in seen and len(relevant) < 8:
                relevant.append(kw)
                seen.add(key)

        search_text = format_search_results(relevant)
        if search_text:
            keyword_context = search_text
            if agent_config.debug:
                _logger.debug("Layer 3 语义检索: %d 条相关记忆（并行优化）", len(relevant))

        # ── 知识库检索（使用公共函数，支持配置）──
        from miniagent.knowledge import retrieve_knowledge_context
        kb_context_str = retrieve_knowledge_context(
            user_input, phase="executor", default_top_k=3, default_max_chars=4000
        )
        # 公共函数返回的字符串已有标题，直接使用
        kb_context: str | None = kb_context_str if kb_context_str else None

        merged_system = build_execution_system_prompt(
            agent_identity=AGENT_IDENTITY,
            caller_system_prompt=system_prompt,
            plan_summary=plan.summary,
            keyword_context=keyword_context,
            kb_context=kb_context,
            session_files_root=agent_config.session_workspace,
        )
        if agent_config.risk_level:
            merged_system += f"\n\n（本任务风险等级：{agent_config.risk_level}）"
        context_manager.init(merged_system, user_input)
        if memory:
            context_manager.inject_memory(memory)
    else:
        # ── 知识库检索（无会话时也检索，使用公共函数）──
        from miniagent.knowledge import retrieve_knowledge_context
        kb_context_str = retrieve_knowledge_context(
            user_input, phase="executor", default_top_k=3, default_max_chars=4000
        )
        kb_context: str | None = kb_context_str if kb_context_str else None

        merged_system = build_execution_system_prompt(
            agent_identity=AGENT_IDENTITY,
            caller_system_prompt=system_prompt,
            plan_summary=plan.summary,
            keyword_context=None,
            kb_context=kb_context,
            session_files_root=agent_config.session_workspace,
        )
        if agent_config.risk_level:
            merged_system += f"\n\n（本任务风险等级：{agent_config.risk_level}）"
        context_manager.init(merged_system, user_input)

    # ── 恢复对话历史（在当前输入之前） ──
    if agent_config.conversation_history:
        # 先保存当前 user_input
        current_user_msg = {"role": "user", "content": user_input}
        hist_api = conversation_history_for_llm(agent_config.conversation_history)
        # 重建消息：system + 历史 + 当前输入
        context_manager._messages = [
            context_manager._messages[0],  # system prompt
            *hist_api,  # 历史消息（含 thinking → assistant 映射）
            current_user_msg,  # 当前输入
        ]
        context_manager._recalculate_tokens()
        if agent_config.debug:
            _logger.debug("恢复对话历史: %d 条消息", len(agent_config.conversation_history))

    max_turns = agent_config.max_turns
    turns_left = max_turns
    loop_warning_shown = False

    # 跟踪工具调用
    turn_tool_calls: list[dict[str, Any]] = []

    # 活动日志 — 记录会话开始
    session_key = agent_config.session_key or "default"
    source = "cli"  # 默认 CLI，飞书调用方会设置 session_key
    al.log_session_start(session_key, user_input, source)

    if agent_config.debug:
        idx_stats = ki.get_stats()
        _logger.info("使用 %d 个工具 (策略: %s)", len(tools), agent_config.tool_selection_strategy)
        _logger.info("计划: %s", plan.summary)
        _logger.info(
            "最大轮数: %d | 循环检测: %s", max_turns, "启用" if loop_config.enabled else "禁用"
        )
        _logger.debug("三层记忆: L3(关键词索引 %d 词)", idx_stats["total_keywords"])

    llm_client = client if client is not None else get_shared_async_openai()

    exec_turn_no = 0
    _exec_hist_segments: dict[str, list[str]] = {}
    _phase_header_sent: set[str] = set()

    sep = _thinking_segment_separator()

    def _joined_phase_cumulative(label: str, current_body: str) -> str:
        """将同一 ``label`` 下历史执行轮正文与 ``current_body`` 用分段符拼接，供思考流 cumulative 展示。

        返回完整累积内容（含历史轮），使引擎端 prefix 检测始终生效。
        工具意图行必须用 ``streaming=False``，否则污染 LLM 正文前缀导致 prefix 匹配失效。
        """
        prev = [p for p in _exec_hist_segments.get(label, []) if (p or "").strip()]
        if not prev:
            return current_body
        return sep.join(prev + [current_body])

    async def _stream_exec_turn(
        merge_overrides: dict[str, Any] | None,
        tools_arg: list[Any],
        thinking_phase_label: str,
        is_last_step: bool = False,
    ) -> tuple[Any, dict[str, Any], int, Any, str, str]:
        """流式调用执行阶段 LLM 一轮，聚合正文与 tool_calls，并驱动 ``on_thinking``。

        Args:
            merge_overrides: 模型参数覆盖（如 thinking_level/budget）
            tools_arg: 本轮可用的工具定义列表（传给 LLM tools 参数）
            thinking_phase_label: 思考流分段标题（如 "[执行]" 或 "[步骤 1/3]"）
            is_last_step: 是否为规划的最后一步（最后一步的 LLM 正文不在思考区显示）

        Returns:
            tuple: (msg, usage, elapsed_ms, tool_calls, full_content, thinking_header)
                - msg: LLM 返回的 assistant 消息对象
                - usage: token 用量统计（prompt/completion/total）
                - elapsed_ms: 本轮调用耗时（毫秒）
                - tool_calls: 解析后的 tool_calls 列表（无则空）
                - full_content: 聚合后的正文内容
                - thinking_header: 当前思考分段标题（供工具回调）
        """
        nonlocal exec_turn_no
        exec_turn_no += 1
        start_ms = time.monotonic_ns() // 1_000_000
        messages = strip_leading_underscore_keys_from_messages(list(context_manager.get_messages()))
        turn_display = exec_turn_no

        if agent_config.debug:
            _logger.debug(
                "LLM 请求 (第 %d 轮): 消息数=%d, 工具数=%d",
                turn_display,
                len(messages),
                len(tools_arg),
            )

        full_content_parts = StreamingBuffer()
        full_tool_calls: list[Any] = []
        thinking_header = thinking_phase_label
        _thinking_started = False
        _tool_call_accum: dict[int, dict[str, str]] = {}
        _usage = None

        # 性能优化：回调频率控制
        _last_callback_time = time.monotonic_ns() // 1_000_000
        _callback_min_interval_ms = get_config("execution.callback_min_interval_ms", 50)  # 50ms最小间隔
        _callback_min_chars = get_config("execution.callback_min_chars", 100)  # 100字符阈值
        _chars_since_last_callback = 0

        if on_thinking and not _thinking_started:
            try:
                if thinking_phase_label not in _phase_header_sent:
                    await invoke_on_thinking(
                        on_thinking,
                        f"{thinking_phase_label} 开始",
                        False,
                        thinking_phase_label,
                        full_record=f"{thinking_phase_label} 开始",
                        is_last_step=is_last_step,
                    )
                    _phase_header_sent.add(thinking_phase_label)
                _thinking_started = True
            except Exception as e:
                _logger.debug("思考状态推送失败（非关键）: %s", e)

        exec_kw = resolve_exec_completion_kwargs(
            agent_config, stream=True, merge_overrides=merge_overrides
        )
        emit_trace(
            {
                "type": "llm.request",
                "phase": "exec",
                "session_key": session_key,
                "turn": turn_display,
                "model": exec_kw["model"],
                "message_count": len(messages),
                "tool_count": len(tools_arg),
            }
        )
        stream = await llm_client.chat.completions.create(
            messages=messages,  # type: ignore[arg-type]
            tools=tools_arg if tools_arg else None,  # type: ignore[arg-type]
            **exec_kw,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if hasattr(chunk, "usage") and chunk.usage:
                _usage = chunk.usage
            if delta.content:
                full_content_parts.append(delta.content)
                _chars_since_last_callback += len(delta.content)

                if on_thinking:
                    # 性能优化：频率控制（避免高频回调）
                    now_ms = time.monotonic_ns() // 1_000_000
                    time_elapsed = now_ms - _last_callback_time

                    # 触发条件：时间间隔 > 50ms 或 字符数 > 100
                    should_callback = (
                        time_elapsed >= _callback_min_interval_ms or
                        _chars_since_last_callback >= _callback_min_chars
                    )

                    if should_callback:
                        cum = _joined_phase_cumulative(thinking_phase_label, full_content_parts.getvalue())
                        try:
                            await invoke_on_thinking(
                                on_thinking,
                                cum,
                                True,
                                thinking_phase_label,
                                full_record=cum,
                                is_last_step=is_last_step,
                            )
                            _last_callback_time = now_ms  # 更新最后回调时间
                            _chars_since_last_callback = 0  # 重置字符计数
                        except Exception:
                            pass
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in _tool_call_accum:
                        _tool_call_accum[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name if tc_delta.function else "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        _tool_call_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            _tool_call_accum[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            _tool_call_accum[idx]["arguments"] += tc_delta.function.arguments

        full_content = full_content_parts.getvalue()

        # 性能优化：确保最后回调发送（完整内容）
        if on_thinking and full_content and _chars_since_last_callback > 0:
            try:
                cum = _joined_phase_cumulative(thinking_phase_label, full_content)
                await invoke_on_thinking(
                    on_thinking,
                    cum,
                    True,
                    thinking_phase_label,
                    full_record=cum,
                    is_last_step=is_last_step,
                )
            except Exception:
                pass

        if _tool_call_accum:
            full_tool_calls = []
            for idx in sorted(_tool_call_accum.keys()):
                tc_info = _tool_call_accum[idx]
                # 预解析 arguments（避免后续重复 JSON 解析）
                try:
                    tc_info["args_dict"] = json.loads(tc_info["arguments"])
                except (json.JSONDecodeError, TypeError):
                    tc_info["args_dict"] = {}
                fn_obj = SimpleNamespace(name=tc_info["name"], arguments=tc_info["arguments"])
                tc_obj = SimpleNamespace(id=tc_info["id"], function=fn_obj)
                # 将 args_dict 附加到 tc_obj 以便后续使用
                tc_obj._args_dict = tc_info["args_dict"]
                full_tool_calls.append(tc_obj)

        msg = SimpleNamespace(
            content=full_content or None,
            tool_calls=full_tool_calls or None,
        )

        if on_thinking and full_tool_calls and _tool_intent_in_thinking_enabled():
            try:
                for tc in full_tool_calls:
                    try:
                        args_dict = tc._args_dict  # 使用预解析的结果
                        intent = _extract_tool_intent(tc.function.name, args_dict)
                    except (json.JSONDecodeError, TypeError):
                        intent = "执行操作"
                    line = f"🔧 {tc.function.name} — {intent}"
                    await invoke_on_thinking(
                        on_thinking,
                        line,
                        False,
                        thinking_phase_label,
                        full_record=line,
                    )
            except Exception as e:
                _logger.debug("思考状态推送失败（非关键）: %s", e)

        emit_trace(
            {
                "type": "llm.response",
                "phase": "exec",
                "session_key": session_key,
                "turn": turn_display,
                "has_tool_calls": bool(full_tool_calls),
                "usage": _usage.model_dump() if _usage else None,
            }
        )

        if agent_config.log_file:
            append_log(
                agent_config.log_file,
                {
                    "phase": "exec",
                    "turn": turn_display,
                    "req": {
                        "model": exec_kw["model"],
                        "messageCount": len(messages),
                        "toolCount": len(tools_arg),
                    },
                    "res": {
                        "hasToolCalls": bool(full_tool_calls),
                        "toolCalls": [
                            {"name": tc.function.name, "args": truncate(tc.function.arguments, 300)}
                            for tc in full_tool_calls
                        ],
                        "content": truncate(full_content or "", 1000) if full_content else None,
                        "usage": _usage.model_dump() if _usage else None,
                    },
                },
            )

        al.log_llm_call(
            session_key=session_key,
            turn=turn_display,
            model=exec_kw["model"],
            message_count=len(messages),
            tool_count=len(tools_arg),
            thinking=full_content,
            token_usage=_usage.model_dump() if _usage else None,
        )
        if (full_content or "").strip():
            _exec_hist_segments.setdefault(thinking_phase_label, []).append(full_content)
        return msg, exec_kw, start_ms, _usage, full_content, thinking_header

    async def _invoke_on_tool_finish(
        name: str,
        args_json: str,
        result: str,
        success: bool,
        thinking_header: str,
    ) -> None:
        """调用 ``on_tool_finish`` 回调。"""
        if on_tool_finish is None:
            return
        try:
            await on_tool_finish(name, args_json, result, success, thinking_header=thinking_header)
        except Exception as e:
            if agent_config.debug:
                _logger.exception("on_tool_finish 回调失败: %s", e)

    async def _run_tool_calls_phase(msg: Any, start_ms: int, thinking_header: str) -> str | None:
        """处理 assistant 消息中的 tool_calls：入上下文、循环检测、并发执行工具并写回 tool 消息。

        Args:
            msg: LLM 返回的 assistant 消息（含 content 与 tool_calls）
            start_ms: 本轮开始时间戳（用于计算 elapsed）
            thinking_header: 当前思考分段标题（传递给工具回调）

        Returns:
            str | None: 上下文超预算时返回错误消息；正常完成返回 None
        """
        nonlocal loop_warning_shown
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        oob_a = _append_context_or_return(context_manager, assistant_msg)
        if oob_a:
            return oob_a

        timeout_sec = max(1, int(agent_config.tool_timeout))
        pending: list[tuple[Any, dict[str, Any], Any]] = []

        for tc in msg.tool_calls:
            tool = effective_registry.get(tc.function.name)
            if tool is None:
                avail = ", ".join(effective_registry.list())
                # 未知工具：LLM 调用了不存在的工具（通常是模型幻觉或工具注册问题）
                try:
                    args_unknown = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args_unknown = {"raw": tc.function.arguments}
                _log_tool_error(
                    tool_name=tc.function.name,
                    tool_call_id=tc.id,
                    args=args_unknown,
                    session_key=session_key,
                    error_type="UnknownTool",
                    error_message=f"工具不存在，可用工具: {avail[:100]}",
                    is_user_error=False,  # 这是 LLM 或工具注册问题，非用户误用
                )
                oob_u = _append_context_or_return(
                    context_manager,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"错误：未知工具 {tc.function.name}。可用: {avail}",
                    },
                )
                if oob_u:
                    return oob_u
                if on_tool_call:
                    on_tool_call(tc.function.name, tc.function.arguments, f"{WARNING_PREFIX} 未知工具")
                await _invoke_on_tool_finish(
                    tc.function.name,
                    tc.function.arguments,
                    f"错误：未知工具 {tc.function.name}。可用: {avail}",
                    False,
                    thinking_header,
                )
                continue

            try:
                args = getattr(tc, "_args_dict", None) or json.loads(tc.function.arguments)
                loop_check = loop_detector.check(tc.function.name, args)

                if loop_check.level == "critical":
                    elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                    monitor.record(tc.function.name, elapsed, False)
                    _logger.warning("循环检测拦截: %s", loop_check.message)
                    return (
                        f"{WARNING_PREFIX} 任务执行被终止：{loop_check.message}\n\n建议：简化请求或明确具体目标。"
                    )

                if loop_check.level == "warning" and not loop_warning_shown:
                    loop_warning_shown = True
                    _logger.warning(loop_check.message)
            except Exception:
                args = {}

            pending.append((tc, args, tool))

        async def _run_tool(
            tc: Any, args: dict[str, Any], tool: Any
        ) -> tuple[Any, dict[str, Any], Any, Any, int]:
            """执行单个 tool_call（含超时与监控），返回 tool 消息构造所需字段。

            Args:
                tc: tool_call 对象（含 id、function.name、function.arguments）
                args: 解析后的工具参数字典
                tool: ToolDefinition 对象（含 handler 与权限信息）

        Returns:
            tuple: (tc, args, result, tool, elapsed_ms)
                - tc: 原 tool_call 对象
                - args: 解析后的参数
                - result: ToolResult（success/content/meta）
                - tool: ToolDefinition
                - elapsed_ms: 执行耗时（毫秒）

            异常处理策略：
            - TimeoutError: 工具执行超过 timeout_sec，返回超时提示
            - Exception: 其他异常（权限拒绝、参数错误、内部错误等），返回错误信息
            无论成功与否，都会记录 trace 和 monitor，不影响其他工具执行。
            """
            # 性能优化：获取并发限制信号量
            semaphore = _get_tool_semaphore()

            async with semaphore:  # 限制并发数
                tool_start = time.monotonic_ns() // 1_000_000
                emit_trace(
                    {
                        "type": "tool.start",
                        "session_key": session_key,
                        "tool": tc.function.name,
                        "concurrent_slots_available": semaphore._value,  # Trace并发槽位
                    }
                )
                try:
                    result = await asyncio.wait_for(
                        tool.handler(args, ctx),
                        timeout=timeout_sec,
                    )
                except asyncio.TimeoutError:
                    # 超时：工具执行时间超过限制（可能是工具性能问题或参数导致的长操作）
                    result = ToolResult(
                        success=False,
                        content=f"{WARNING_PREFIX} 工具超时（{timeout_sec}s）: {tc.function.name}",
                        meta={"error_type": "TimeoutError"},
                    )
                    _log_tool_error(
                        tool_name=tc.function.name,
                        tool_call_id=tc.id,
                        args=args,
                        session_key=session_key,
                        error_type="TimeoutError",
                        error_message=f"工具执行超过 {timeout_sec}s 超时限制",
                        is_user_error=False,  # 超时可能是工具性能问题，也可能是用户请求导致
                    )
                except PermissionError as e:
                    # 权限拒绝：沙箱限制或文件权限不足（用户误用）
                    result = ToolResult(
                        success=False,
                        content=f"{WARNING_PREFIX} 权限拒绝: {e}",
                        meta={"error_type": "PermissionError"},
                    )
                    _log_tool_error(
                        tool_name=tc.function.name,
                        tool_call_id=tc.id,
                        args=args,
                        session_key=session_key,
                        error_type="PermissionError",
                        error_message=str(e),
                        is_user_error=True,  # 权限问题通常是用户操作导致的
                    )
                except FileNotFoundError as e:
                    # 文件不存在：read_file 等工具的常见错误（用户误用）
                    result = ToolResult(
                        success=False,
                        content=f"{WARNING_PREFIX} 文件不存在: {e}",
                        meta={"error_type": "FileNotFoundError"},
                    )
                    _log_tool_error(
                        tool_name=tc.function.name,
                        tool_call_id=tc.id,
                        args=args,
                        session_key=session_key,
                        error_type="FileNotFoundError",
                        error_message=str(e),
                        is_user_error=True,  # 文件不存在是用户操作导致的
                    )
                except Exception as e:
                    # 其他异常：参数错误、工具内部错误等，需要详细诊断
                    error_type_name = type(e).__name__
                    result = ToolResult(
                        success=False,
                        content=f"{WARNING_PREFIX} 执行异常: {e}",
                        meta={"error_type": error_type_name},
                    )
                    tb_str = traceback.format_exc()
                    # 根据异常类型判断是用户误用还是工具缺陷
                    is_user_error = isinstance(e, (
                        ValueError,  # 参数错误
                        TypeError,   # 类型错误
                        KeyError,    # 键错误
                        json.JSONDecodeError,  # JSON 解析错误
                    ))
                    _log_tool_error(
                        tool_name=tc.function.name,
                        tool_call_id=tc.id,
                        args=args,
                        session_key=session_key,
                        error_type=error_type_name,
                        error_message=str(e),
                        is_user_error=is_user_error,
                        traceback_str=tb_str if not is_user_error else None,
                    )
                tool_elapsed = time.monotonic_ns() // 1_000_000 - tool_start
                emit_trace(
                    {
                        "type": "tool.end",
                        "session_key": session_key,
                        "tool": tc.function.name,
                        "duration_ms": tool_elapsed,
                        "success": result.success,
                    }
                )
                return tc, args, tool, result, tool_elapsed

        if pending:
            if agent_config.allow_parallel_tools and len(pending) > 1:
                # 使用 return_exceptions=True 确保单个工具失败不影响其他工具
                outcomes = await asyncio.gather(
                    *[_run_tool(tc, args, tool) for tc, args, tool in pending],
                    return_exceptions=True
                )
            else:
                outcomes = []
                for tc, args, tool in pending:
                    try:
                        outcomes.append(await _run_tool(tc, args, tool))
                    except Exception as e:
                        # 捕获单个工具异常，继续执行其他工具
                        outcomes.append(e)

            for idx, outcome in enumerate(outcomes):
                tc, args, tool = pending[idx]
                # 处理可能的异常结果
                if isinstance(outcome, Exception):
                    result = ToolResult(success=False, content=f"工具执行异常: {outcome}")
                    tool_elapsed = 0
                else:
                    # outcome 是 _run_tool 返回的元组，只提取 result 和 tool_elapsed
                    # tc/args/tool 已经从 pending 获取
                    _, _, _, result, tool_elapsed = outcome
                turn_tool_calls.append(
                    {
                        "name": tc.function.name,
                        "args": tc.function.arguments,
                        "result": result.content,
                    }
                )
                loop_detector.record(tc.function.name, args, result.content)
                monitor.record(tc.function.name, tool_elapsed, result.success)
                oob_t = _append_context_or_return(
                    context_manager,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.content,
                    },
                )
                intent = _extract_tool_intent(tc.function.name, args)
                # 从 result.meta 中提取 error_type（如果失败）
                error_type = result.meta.get("error_type") if not result.success else None
                al.log_tool_call(
                    session_key=session_key,
                    tool_name=tc.function.name,
                    intent=intent,
                    args=args,
                    result=result.content,
                    duration_ms=tool_elapsed,
                    success=result.success,
                    error_type=error_type,
                )
                await _invoke_on_tool_finish(
                    tc.function.name,
                    tc.function.arguments,
                    result.content,
                    result.success,
                    thinking_header,
                )
                if oob_t:
                    return oob_t
        return None

    use_phased = _env_phased_execution_enabled() and bool(plan.steps)

    if not use_phased:
        while turns_left > 0:
            turns_left -= 1
            msg, _exec_kw, start_ms, _usage, _full_content, turn_label = await _stream_exec_turn(
                None, tools, "[执行]", is_last_step=True
            )

            if not msg.tool_calls:
                final_reply = msg.content or "(空回复)"
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                monitor.record("llm_response", elapsed, True)
                oob = _append_context_or_return(
                    context_manager, {"role": "assistant", "content": final_reply}
                )
                if oob:
                    return oob

                if agent_config.session_key and final_reply:
                    await _save_session_memory(
                        ms,
                        agent_config.session_key,
                        user_input,
                        final_reply,
                        turn_tool_calls,
                    )
                    al.log_final_reply(session_key, final_reply)

                if agent_config.debug:
                    _logger.debug(context_manager.get_token_report())

                return final_reply

            early = await _run_tool_calls_phase(msg, start_ms, turn_label)
            if early is not None:
                return early
            turn_tool_calls.clear()
    else:

        async def _finish_phased_text_turn(
            final_reply: str, start_ms_text: int, *, save_memory: bool
        ) -> str | None:
            """写入本轮纯文本 assistant；可选落会话记忆。若上下文超预算则返回错误文案。"""
            elapsed_txt = time.monotonic_ns() // 1_000_000 - start_ms_text
            monitor.record("llm_response", elapsed_txt, True)
            oob_txt = _append_context_or_return(
                context_manager, {"role": "assistant", "content": final_reply}
            )
            if oob_txt:
                return oob_txt
            if save_memory and agent_config.session_key and final_reply:
                await _save_session_memory(
                    ms,
                    agent_config.session_key,
                    user_input,
                    final_reply,
                    turn_tool_calls,
                )
                al.log_final_reply(session_key, final_reply)
            if agent_config.debug:
                _logger.debug(context_manager.get_token_report())
            return None

        n_steps = len(plan.steps)
        for si, step in enumerate(plan.steps):
            phase_lbl = _step_thinking_header(si, n_steps, step)
            is_last = si == n_steps - 1
            step_tools = _resolve_exec_tools(effective_registry, agent_config, plan, step)
            context_manager.set_tools(step_tools)
            step_hint = (
                f"[执行步骤 {step.step_number or si + 1}/{n_steps}] {step.description}\n"
                f"预期输入：{step.expected_input}\n"
                f"预期产出：{step.expected_output}\n"
                "请仅完成本步骤；若当前无需工具，请直接给出简短步骤小结。"
            )
            oob_step = _append_context_or_return(
                context_manager, {"role": "user", "content": step_hint}
            )
            if oob_step:
                return oob_step

            sub_cap = min(_step_max_turns_cap(), turns_left)
            sub_left = sub_cap
            stl, stb = map_business_depth(step.thinking_level)
            step_merge = {"thinking_level": stl, "thinking_budget": stb}

            step_resolved = False
            while sub_left > 0 and turns_left > 0:
                turns_left -= 1
                sub_left -= 1
                msg, _ek, start_ms, _u, _fc, turn_label = await _stream_exec_turn(
                    step_merge, step_tools, phase_lbl, is_last_step=is_last
                )

                if not msg.tool_calls:
                    final_reply = msg.content or "(空回复)"
                    oob_txt = await _finish_phased_text_turn(
                        final_reply, start_ms, save_memory=is_last
                    )
                    if oob_txt is not None:
                        return oob_txt
                    if is_last:
                        return final_reply
                    step_resolved = True
                    break

                early = await _run_tool_calls_phase(msg, start_ms, turn_label)
                if early is not None:
                    return early
                turn_tool_calls.clear()

            if is_last and not step_resolved:
                if turns_left > 0:
                    oob_g = _append_context_or_return(
                        context_manager,
                        {
                            "role": "user",
                            "content": (
                                "（系统：本步单步子轮次已用尽；工具结果已在上下文中。"
                                "请仅用自然语言给出本步的最终简短小结，不要调用工具。）"
                            ),
                        },
                    )
                    if oob_g:
                        return oob_g
                    turns_left -= 1
                    msg_g, _, start_ms_g, _, _, _ = await _stream_exec_turn(
                        step_merge, [], phase_lbl, is_last_step=True
                    )
                    if not msg_g.tool_calls:
                        final_reply = msg_g.content or "(空回复)"
                        oob_txt = await _finish_phased_text_turn(
                            final_reply, start_ms_g, save_memory=True
                        )
                        if oob_txt is not None:
                            return oob_txt
                        return final_reply
                return (
                    f"{WARNING_PREFIX} 最后一步在单步子轮次（MINIAGENT_STEP_MAX_TURNS）或总轮数限制内，"
                    "未以「无工具调用」形式结束。\n\n"
                    "可提高 MINIAGENT_STEP_MAX_TURNS、MINIAGENT_AGENT_MAX_TURNS，"
                    "或设置 MINIAGENT_PHASED_EXECUTION=0 退回单循环执行后重试。"
                )

            if not is_last and not step_resolved and turns_left > 0:
                oob_n = _append_context_or_return(
                    context_manager,
                    {
                        "role": "user",
                        "content": (
                            "（系统提示：上一步在单步子轮次内未结束，以下继续下一步；"
                            "若结果不理想可适当提高 MINIAGENT_STEP_MAX_TURNS。）"
                        ),
                    },
                )
                if oob_n:
                    return oob_n

    # ── 达到最大轮数 ──
    loop_stats = loop_detector.get_stats()

    if agent_config.session_key:
        al.log_incomplete(session_key, f"达到最大轮数 {max_turns}")

    if agent_config.debug:
        _logger.debug(context_manager.get_token_report())

    return (
        f"{WARNING_PREFIX} 达到最大调用次数（{max_turns} 轮），任务未完成。\n\n"
        f"建议：简化请求，分步骤执行。\n\n"
        f"📊 本轮统计：工具调用 {loop_stats['total_calls']} 次"
    )


# ─── 工具意图提取 ────────────────────────────────────────────


def _clip_intent_value(s: str) -> str:
    """将意图字符串截断至 :func:`_tool_intent_max_chars` 上限并追加长度提示。"""
    cap = _tool_intent_max_chars()
    if cap <= 0:
        return s
    if len(s) <= cap:
        return s
    return s[:cap] + f"…（共 {len(s)} 字）"


def _extract_tool_intent(tool_name: str, args: dict[str, Any]) -> str:
    """从工具调用中提取简要意图描述。"""
    base_intent = _TOOL_INTENT_MAP.get(tool_name, f"调用 {tool_name}")
    if args:
        for key in ("path", "query", "command", "content", "url"):
            if key in args:
                val = _clip_intent_value(str(args[key]))
                return f"{base_intent}: {val}"
    return base_intent


# ─── 记忆保存 ────────────────────────────────────────────


async def _save_session_memory(
    memory_store: MemoryStoreProtocol,
    session_key: str,
    user_input: str,
    final_reply: str,
    turn_tool_calls: list[dict[str, Any]],
) -> None:
    """保存会话记忆：提取事实、生成摘要、写入存储。"""
    from datetime import datetime, timezone

    facts = extract_facts(user_input + " " + final_reply)
    summary = generate_turn_summary(user_input, turn_tool_calls, final_reply)
    now = datetime.now(timezone.utc).isoformat()

    await memory_store.update_summary(session_key, summary, facts)
    await memory_store.add_entry(
        session_key,
        MemoryEntryInput(
            timestamp=now,
            user_snippet=user_input[:100],
            summary=summary,
            facts=facts,
        ),
    )
    flush_ki = getattr(memory_store, "flush_keyword_index", None)
    if callable(flush_ki):
        flush_ki()


__all__ = [
    "execute_plan",
    "get_client",
    "AGENT_IDENTITY",
    "build_execution_system_prompt",
]
