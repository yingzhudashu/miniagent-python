"""运行时基础设施

导出：
- 日志系统 (logger)
- 工具注册表 (registry)
- 性能监控 (monitor)
- 循环检测 (loop_detector)
- 实例管理 (instance)
- 进程跟踪 (process)
- 消息队列 (MessageQueueManager)
- 通道路由 (ChannelRouter)
"""

from miniagent.infrastructure.logger import get_logger, append_log, truncate
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.infrastructure.loop_detector import LoopDetector
from miniagent.infrastructure.instance import (
    InstanceRegistry,
    register_instance,
    update_instance_mode,
    heartbeat,
    unregister_instance,
    list_instances,
    stop_instance_by_id,
    format_instances_table,
)
from miniagent.infrastructure.process import (
    cleanup_all_processes,
    create_tracked_subprocess,
    register_process,
    deregister_process,
    get_tracked_count,
    get_active_processes,
)
from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode
from miniagent.infrastructure.channel_router import ChannelRouter

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
]
