"""运行时基础设施（跨通道共享的「副作用与资源」）

与 ``miniagent.memory`` 的区别：本包侧重 **工具注册、监控、进程与子进程、多实例注册表、
消息队列与通道路由** 等运行期横切能力；记忆内容持久化与检索在 ``memory`` 包。
多实例注册与 PID 语义见 ``docs/INSTANCE_REGISTRY.md``；通道绑定见 ``docs/CHANNEL_BINDING.md``。

本 ``__init__`` 聚合最常用的符号；下列子模块按需直接导入：

- ``tracing``：``emit_trace`` / 进程内 trace 钩子（可选接 APM）
- ``feishu_inbound_lock``：飞书 WebSocket 入站单进程独占锁

聚合导出：

- 日志 (``logger``)、工具注册表 (``registry``)、监控 (``monitor``)
- 循环检测 (``loop_detector``)、实例注册表 (``instance``)、进程跟踪 (``process``)
- 消息队列 (``MessageQueueManager``)、通道路由 (``ChannelRouter``)
"""

from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.instance import (
    InstanceRegistry,
    format_instances_markdown,
    format_instances_table,
    heartbeat,
    list_instances,
    register_instance,
    stop_instance_by_id,
    unregister_instance,
    update_instance_mode,
)
from miniagent.infrastructure.logger import append_log, get_logger, truncate
from miniagent.infrastructure.loop_detector import LoopDetector
from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.process import (
    cleanup_all_processes,
    create_tracked_subprocess,
    deregister_process,
    get_active_processes,
    get_tracked_count,
    register_process,
)
from miniagent.infrastructure.registry import DefaultToolRegistry

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
