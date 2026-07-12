"""运行时基础设施（跨通道共享的「副作用与资源」）

与 ``miniagent.memory`` 的区别：本包侧重 **工具注册、监控、进程与子进程、多实例注册表、
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
    "ChannelRouter": "miniagent.infrastructure.channel_router",
    "DefaultToolMonitor": "miniagent.infrastructure.monitor",
    "DefaultToolRegistry": "miniagent.infrastructure.registry",
    "InstanceRegistry": "miniagent.infrastructure.instance",
    "LoopDetector": "miniagent.infrastructure.loop_detector",
    "MessageQueueManager": "miniagent.infrastructure.message_queue",
    "QueueMode": "miniagent.infrastructure.message_queue",
    "append_log": "miniagent.infrastructure.logger",
    "cleanup_all_processes": "miniagent.infrastructure.process",
    "create_tracked_subprocess": "miniagent.infrastructure.process",
    "deregister_process": "miniagent.infrastructure.process",
    "format_instances_markdown": "miniagent.infrastructure.instance",
    "format_instances_table": "miniagent.infrastructure.instance",
    "get_active_processes": "miniagent.infrastructure.process",
    "get_logger": "miniagent.infrastructure.logger",
    "get_tracked_count": "miniagent.infrastructure.process",
    "heartbeat": "miniagent.infrastructure.instance",
    "list_instances": "miniagent.infrastructure.instance",
    "register_instance": "miniagent.infrastructure.instance",
    "register_process": "miniagent.infrastructure.process",
    "stop_instance_by_id": "miniagent.infrastructure.instance",
    "truncate": "miniagent.infrastructure.logger",
    "unregister_instance": "miniagent.infrastructure.instance",
    "update_instance_mode": "miniagent.infrastructure.instance",
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
