"""聚合导出层 — 统一入口点

历史：原为单文件 ``unified.py``，逻辑已拆入 ``miniagent.engine`` 等子包。
本模块保留常用符号的单一导入入口；**新代码优先**从源子包导入（见下表）。

``unified_entry`` 内会先 ``load_secrets_from_project_root()`` 再构造 ``RuntimeContext``。
配置通过 JSON 传递（``config.defaults.json`` / ``config.user.json``）。

启动编排与异步主流程见 ``docs/ARCHITECTURE.md``（``unified_entry`` → ``unified_main``）。

符号映射（``__all__`` → 推荐导入路径）：

+---------------------------+------------------------------------------+--------+
| 符号                      | 推荐路径                                 | async  |
+===========================+==========================================+========+
| ``RuntimeContext``        | ``miniagent.runtime.context``            |        |
| ``FeishuRuntime``         | ``miniagent.engine.feishu_state``        |        |
| ``UnifiedEngine``         | ``miniagent.engine.engine``              |        |
| ``ThinkingDisplay``       | ``miniagent.engine.thinking``            |        |
| ``init_subsystems``       | ``miniagent.engine.init``                | ✓      |
| ``unified_main``          | ``miniagent.engine.main``                | ✓      |
| ``run_cli_loop``          | ``miniagent.engine.main``                | ✓      |
| ``unified_entry``         | ``miniagent.compat``（进程入口专用）     |        |
| ``try_lock_session``      | ``miniagent.engine.session_lock``        |        |
| ``try_lock_session_async``| ``miniagent.engine.session_lock``        | ✓      |
| ``release_session_lock``  | ``miniagent.engine.session_lock``        |        |
| ``is_session_locked``     | ``miniagent.engine.session_lock``        |        |
| ``cmd_session_list``      | ``miniagent.engine.cli_commands``        |        |
| ``cmd_session_switch``    | ``miniagent.engine.cli_commands``        | ✓      |
| ``cmd_session_create``    | ``miniagent.engine.cli_commands``        | ✓      |
| ``cmd_session_rename``    | ``miniagent.engine.cli_commands``        |        |
| ``cmd_queue_status``      | ``miniagent.engine.cli_commands``        |        |
| ``cmd_queue_set``         | ``miniagent.engine.cli_commands``        | ✓      |
| ``cmd_help``              | ``miniagent.engine.cli_commands``        |        |
| ``get_version``           | ``miniagent.engine.welcome``             |        |
| ``get_session_display``   | ``miniagent.engine.welcome``             |        |
| ``print_welcome``         | ``miniagent.engine.welcome``             |        |
+---------------------------+------------------------------------------+--------+

标记为 async 的符号须在 ``async def`` 内 ``await``，或由 ``asyncio.run`` / 事件循环调度。
运行时集合见 :data:`COMPAT_ASYNC_EXPORTS`。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# 与模块 docstring 表格一致；供测试校验 async 标注是否过期。
COMPAT_ASYNC_EXPORTS: frozenset[str] = frozenset(
    {
        "init_subsystems",
        "unified_main",
        "run_cli_loop",
        "try_lock_session_async",
        "cmd_session_switch",
        "cmd_session_create",
        "cmd_queue_set",
    }
)

# ── CLI Commands ──
from miniagent.engine.cli_commands import (
    cmd_help,
    cmd_queue_set,
    cmd_queue_status,
    cmd_session_create,
    cmd_session_list,
    cmd_session_rename,
    cmd_session_switch,
)

# ── Core Engine ──
from miniagent.engine.engine import UnifiedEngine

# ── Feishu Runtime（实例由 RuntimeContext.feishu 持有）──
from miniagent.engine.feishu_state import FeishuRuntime

# ── Initialization ──
from miniagent.engine.init import init_subsystems

# ── Main Entry ──
from miniagent.engine.main import run_cli_loop, unified_main

# ── Session Lock ──
from miniagent.engine.session_lock import (
    is_session_locked,
    release_session_lock,
    try_lock_session,
    try_lock_session_async,
)

# ── Thinking Display ──
from miniagent.engine.thinking import ThinkingDisplay

# ── Welcome ──
from miniagent.engine.welcome import get_session_display, get_version, print_welcome
from miniagent.runtime.context import RuntimeContext


def unified_entry() -> None:
    """统一入口点（由 ``__main__`` 调用）。

    流程概要：

    1. 首次配置引导（无 ``config.user.json`` 时可选交互写入）。
    2. 拉取进程级默认记忆三元组（与 ``MINIAGENT_PATHS_STATE_DIR`` / ``workspaces`` 根一致）。
    3. 构造通道无关的基础设施：消息队列、通道路由器、飞书运行时壳。
    4. 组装 :class:`RuntimeContext`（组合根）：工具注册表、监控、技能、ClawHub、引擎、
       记忆与共享 ``AsyncOpenAI`` 客户端等。
    5. ``asyncio.run(unified_main(ctx))`` 进入异步主流程；会话与工具加载等在
       ``unified_main`` / ``init_subsystems`` 中完成。

    依赖注入优于模块级全局：CLI、飞书 handler 与命令调度通过 ``ctx`` 或闭包访问上述对象。
    """
    import asyncio

    # 首次配置引导须在加载凭据与 LLM 客户端之前执行
    from miniagent.engine.setup_wizard import run_interactive_setup

    run_interactive_setup()

    from miniagent.infrastructure.env_loader import load_secrets_from_project_root

    load_secrets_from_project_root()

    from miniagent.core.openai_client import get_shared_async_openai
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.json_config import get_config
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.memory.defaults import get_process_default_memory_bundle
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client

    # 与 unified_entry 使用同一状态根，避免记忆层与实例注册表路径不一致
    memory_store, activity_log, keyword_index = get_process_default_memory_bundle()

    mq = MessageQueueManager()
    # 从配置文件读取队列模式（合法值：queue / preemptive）
    queue_mode_str = get_config("agent.queue_mode", "queue").lower()
    if queue_mode_str == "preemptive":
        mq.mode = QueueMode.PREEMPTIVE
    elif queue_mode_str == "queue":
        mq.mode = QueueMode.QUEUE
    else:
        _logger.warning(
            "未知 agent.queue_mode=%r，回落到 queue；可用: queue, preemptive",
            queue_mode_str,
        )
        mq.mode = QueueMode.QUEUE

    router = ChannelRouter()
    router.load()
    feishu_rt = FeishuRuntime(mq)

    ctx = RuntimeContext(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=router,
        message_queue=mq,
        feishu=feishu_rt,
        memory_store=memory_store,
        activity_log=activity_log,
        keyword_index=keyword_index,
        openai_client=get_shared_async_openai(),
    )
    asyncio.run(unified_main(ctx))


__all__ = [
    "RuntimeContext",
    "FeishuRuntime",
    "try_lock_session",
    "try_lock_session_async",
    "release_session_lock",
    "is_session_locked",
    "ThinkingDisplay",
    "UnifiedEngine",
    "cmd_session_list",
    "cmd_session_switch",
    "cmd_session_create",
    "cmd_session_rename",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
    "init_subsystems",
    "unified_main",
    "run_cli_loop",
    "get_version",
    "get_session_display",
    "print_welcome",
    "unified_entry",
]
