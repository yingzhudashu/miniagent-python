"""运行时基础设施（跨通道共享的「副作用与资源」）

与 ``miniagent.assistant.memory`` 的区别：本包侧重 **工具注册、监控、进程与子进程、多实例注册表、
消息队列与通道路由** 等运行期横切能力；记忆内容持久化与检索在 ``memory`` 包。
多实例注册与 PID 语义见 ``docs/ENGINEERING.md`` §3.3；通道绑定见 ``docs/FEISHU.md`` §通道绑定。

本 ``__init__`` 聚合最常用的符号；下列子模块按需直接导入：

- ``tracing``：``emit_trace`` / 进程内 trace 钩子（可选接 APM）
- ``feishu_inbound_lock``：飞书 WebSocket 入站单进程独占锁

聚合导出：

- 日志 (``logger``)、工具注册表 (``registry``)、监控 (``monitor``)
- 循环检测 (``loop_detector``)、实例注册表 (``instance``)、进程跟踪 (``process``)
- 消息队列 (``MessageQueueManager``)、通道路由 (``ChannelRouter``)
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "ChannelRouter": "miniagent.assistant.infrastructure.channel_router",
    "DefaultToolMonitor": "miniagent.agent.monitor",
    "DefaultToolRegistry": "miniagent.assistant.infrastructure.registry",
    "InstanceRegistry": "miniagent.assistant.infrastructure.instance",
    "LoopDetector": "miniagent.agent.loop_detector",
    "MessageQueueManager": "miniagent.assistant.infrastructure.message_queue",
    "QueueMode": "miniagent.assistant.infrastructure.message_queue",
    "append_log": "miniagent.agent.logging",
    "cleanup_all_processes": "miniagent.assistant.infrastructure.process",
    "create_tracked_subprocess": "miniagent.assistant.infrastructure.process",
    "deregister_process": "miniagent.assistant.infrastructure.process",
    "format_instances_markdown": "miniagent.assistant.infrastructure.instance",
    "format_instances_table": "miniagent.assistant.infrastructure.instance",
    "get_active_processes": "miniagent.assistant.infrastructure.process",
    "get_logger": "miniagent.agent.logging",
    "get_tracked_count": "miniagent.assistant.infrastructure.process",
    "heartbeat": "miniagent.assistant.infrastructure.instance",
    "list_instances": "miniagent.assistant.infrastructure.instance",
    "register_instance": "miniagent.assistant.infrastructure.instance",
    "register_process": "miniagent.assistant.infrastructure.process",
    "stop_instance_by_id": "miniagent.assistant.infrastructure.instance",
    "truncate": "miniagent.agent.logging",
    "unregister_instance": "miniagent.assistant.infrastructure.instance",
    "update_instance_mode": "miniagent.assistant.infrastructure.instance",
}


def __getattr__(name: str) -> Any:
    """Load historical aggregate exports only when explicitly requested."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy aggregate names to interactive discovery and documentation."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    "get_logger",
    "append_log",
    "truncate",
    "DefaultToolRegistry",
    "DefaultToolMonitor",
    "LoopDetector",
    "InstanceRegistry",
    "register_instance",
    "update_instance_mode",
    "heartbeat",
    "unregister_instance",
    "list_instances",
    "stop_instance_by_id",
    "format_instances_table",
    "cleanup_all_processes",
    "create_tracked_subprocess",
    "register_process",
    "deregister_process",
    "get_tracked_count",
    "get_active_processes",
    "MessageQueueManager",
    "QueueMode",
    "ChannelRouter",
    "format_instances_markdown",
]
