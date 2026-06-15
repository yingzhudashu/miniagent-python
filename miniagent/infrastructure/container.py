"""依赖注入容器。

管理全局实例的创建和注入，替代直接全局变量。

设计原则：
- 单例模式：全局实例使用单例模式避免重复创建
- 延迟初始化：实例在首次访问时初始化（lazy load）
- 线程安全：使用线程锁保护全局实例初始化
- 可替换：支持set_*函数替换全局实例（用于测试）

Phase 3重构：替代miniagent/__init__.py、miniagent/session/__init__.py等全局状态。

进程入口应调用 :func:`bootstrap_default_factories` 注册默认工厂后再 ``get_*`` 获取实例。
会话级 ``DefaultToolRegistry`` 克隆仍由 ``SessionManager`` 管理，与容器单例并存。

详见 docs/ARCHITECTURE.md（依赖注入架构）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from miniagent.types.protocols import (
    ActivityLogProtocol,
    KeywordIndexProtocol,
    MemoryStoreProtocol,
    SessionManagerProtocol,
    ToolMonitorProtocol,
    ToolRegistryProtocol,
)

_logger = __import__("miniagent.infrastructure.logger", fromlist=["get_logger"]).get_logger(__name__)

T = TypeVar("T")


# ============================================================================
# DependencyContainer - 依赖注入容器
# ============================================================================


class DependencyContainer:
    """依赖注入容器（简单实现）。

    管理全局实例的创建和注入，替代直接全局变量。

    Attributes:
        _instances: 实例缓存字典
        _factories: 工厂函数字典
        _lock: 线程锁（保护实例初始化）

    Example:
        container = DependencyContainer()

        # 注册工厂
        container.register_factory(
            ToolRegistryProtocol,
            lambda: ToolRegistry()
        )

        # 获取实例
        registry = container.get(ToolRegistryProtocol)
    """

    def __init__(self):
        """初始化容器（创建实例缓存和锁）。"""
        self._instances: dict[type, Any] = {}
        self._factories: dict[type, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    def register_factory(
        self,
        interface: type[T],
        factory: Callable[[], T],
    ) -> None:
        """注册工厂函数。

        Args:
            interface: Protocol接口类型
            factory: 工厂函数（返回实例）
        """
        with self._lock:
            self._factories[interface] = factory

    def get(self, interface: type[T]) -> T:
        """获取实例（延迟初始化）。

        Args:
            interface: Protocol接口类型

        Returns:
            实例对象

        Raises:
            ValueError: 未注册工厂函数
        """
        # 双重检查锁定（性能优化）
        if interface in self._instances:
            return self._instances[interface]

        with self._lock:
            # 再次检查（避免重复初始化）
            if interface in self._instances:
                return self._instances[interface]

            # 获取工厂函数
            if interface not in self._factories:
                raise ValueError(f"No factory registered for {interface.__name__}")

            # 初始化实例
            factory = self._factories[interface]
            instance = factory()
            self._instances[interface] = instance

            return instance

    def set(self, interface: type[T], instance: T) -> None:
        """设置实例（用于测试注入）。

        Args:
            interface: Protocol接口类型
            instance: 实例对象
        """
        with self._lock:
            self._instances[interface] = instance

    def clear(self) -> None:
        """清空所有实例（用于测试清理）。"""
        with self._lock:
            self._instances.clear()


# ============================================================================
# 全局容器实例
# ============================================================================


_container = DependencyContainer()
_bootstrapped = False


def bootstrap_default_factories() -> None:
    """注册进程级默认 DI 工厂（幂等，由 ``unified_entry`` 调用）。

    当前注册：``ToolRegistryProtocol``、``ToolMonitorProtocol``。
    记忆/会话等仍由 ``RuntimeContext`` 显式构造，避免与状态目录生命周期耦合。
    """
    global _bootstrapped
    if _bootstrapped:
        return
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry

    register_tool_registry_factory(lambda: DefaultToolRegistry())
    register_tool_monitor_factory(lambda: DefaultToolMonitor())
    _bootstrapped = True


def reset_bootstrap_for_tests() -> None:
    """清空容器与 bootstrap 标记（仅供测试）。"""
    global _bootstrapped
    clear_container()
    _bootstrapped = False


# ============================================================================
# 工具注册表API
# ============================================================================


def register_tool_registry_factory(factory: Callable[[], ToolRegistryProtocol]) -> None:
    """注册工具注册表工厂函数。

    Args:
        factory: 工厂函数（返回ToolRegistryProtocol实例）
    """
    _container.register_factory(ToolRegistryProtocol, factory)


def get_tool_registry() -> ToolRegistryProtocol:
    """获取工具注册表（依赖注入）。

    Returns:
        ToolRegistryProtocol实例

    Note:
        替代miniagent/__init__.py中的get_global_tool_registry()。
    """
    return _container.get(ToolRegistryProtocol)


def set_tool_registry(registry: ToolRegistryProtocol) -> None:
    """设置工具注册表（测试注入）。

    Args:
        registry: ToolRegistryProtocol实例
    """
    _container.set(ToolRegistryProtocol, registry)


# ============================================================================
# 会话管理器API
# ============================================================================


def register_session_manager_factory(factory: Callable[[], SessionManagerProtocol]) -> None:
    """注册会话管理器工厂函数。

    Args:
        factory: 工厂函数（返回SessionManagerProtocol实例）
    """
    _container.register_factory(SessionManagerProtocol, factory)


def get_session_manager() -> SessionManagerProtocol:
    """获取会话管理器（依赖注入）。

    Returns:
        SessionManagerProtocol实例
    """
    return _container.get(SessionManagerProtocol)


def set_session_manager(manager: SessionManagerProtocol) -> None:
    """设置会话管理器（测试注入）。

    Args:
        manager: SessionManagerProtocol实例
    """
    _container.set(SessionManagerProtocol, manager)


# ============================================================================
# 记忆存储API
# ============================================================================


def register_memory_store_factory(factory: Callable[[], MemoryStoreProtocol]) -> None:
    """注册记忆存储工厂函数。

    Args:
        factory: 工厂函数（返回MemoryStoreProtocol实例）
    """
    _container.register_factory(MemoryStoreProtocol, factory)


def get_memory_store() -> MemoryStoreProtocol:
    """获取记忆存储（依赖注入）。

    Returns:
        MemoryStoreProtocol实例
    """
    return _container.get(MemoryStoreProtocol)


def set_memory_store(store: MemoryStoreProtocol) -> None:
    """设置记忆存储（测试注入）。

    Args:
        store: MemoryStoreProtocol实例
    """
    _container.set(MemoryStoreProtocol, store)


# ============================================================================
# 活动日志API
# ============================================================================


def register_activity_log_factory(factory: Callable[[], ActivityLogProtocol]) -> None:
    """注册活动日志工厂函数。

    Args:
        factory: 工厂函数（返回ActivityLogProtocol实例）
    """
    _container.register_factory(ActivityLogProtocol, factory)


def get_activity_log() -> ActivityLogProtocol:
    """获取活动日志（依赖注入）。

    Returns:
        ActivityLogProtocol实例
    """
    return _container.get(ActivityLogProtocol)


def set_activity_log(log: ActivityLogProtocol) -> None:
    """设置活动日志（测试注入）。

    Args:
        log: ActivityLogProtocol实例
    """
    _container.set(ActivityLogProtocol, log)


# ============================================================================
# 关键词索引API
# ============================================================================


def register_keyword_index_factory(factory: Callable[[], KeywordIndexProtocol]) -> None:
    """注册关键词索引工厂函数。

    Args:
        factory: 工厂函数（返回KeywordIndexProtocol实例）
    """
    _container.register_factory(KeywordIndexProtocol, factory)


def get_keyword_index() -> KeywordIndexProtocol:
    """获取关键词索引（依赖注入）。

    Returns:
        KeywordIndexProtocol实例
    """
    return _container.get(KeywordIndexProtocol)


def set_keyword_index(index: KeywordIndexProtocol) -> None:
    """设置关键词索引（测试注入）。

    Args:
        index: KeywordIndexProtocol实例
    """
    _container.set(KeywordIndexProtocol, index)


# ============================================================================
# 工具监控API
# ============================================================================


def register_tool_monitor_factory(factory: Callable[[], ToolMonitorProtocol]) -> None:
    """注册工具监控工厂函数。

    Args:
        factory: 工厂函数（返回ToolMonitorProtocol实例）
    """
    _container.register_factory(ToolMonitorProtocol, factory)


def get_tool_monitor() -> ToolMonitorProtocol:
    """获取工具监控（依赖注入）。

    Returns:
        ToolMonitorProtocol实例
    """
    return _container.get(ToolMonitorProtocol)


def set_tool_monitor(monitor: ToolMonitorProtocol) -> None:
    """设置工具监控（测试注入）。

    Args:
        monitor: ToolMonitorProtocol实例
    """
    _container.set(ToolMonitorProtocol, monitor)


# ============================================================================
# 清理函数（测试用）
# ============================================================================


def clear_container() -> None:
    """清空容器（用于测试清理）。"""
    _container.clear()


__all__ = [
    "DependencyContainer",
    # 工具注册表API
    "register_tool_registry_factory",
    "get_tool_registry",
    "set_tool_registry",
    # 会话管理器API
    "register_session_manager_factory",
    "get_session_manager",
    "set_session_manager",
    # 记忆存储API
    "register_memory_store_factory",
    "get_memory_store",
    "set_memory_store",
    # 活动日志API
    "register_activity_log_factory",
    "get_activity_log",
    "set_activity_log",
    # 关键词索引API
    "register_keyword_index_factory",
    "get_keyword_index",
    "set_keyword_index",
    # 工具监控API
    "register_tool_monitor_factory",
    "get_tool_monitor",
    "set_tool_monitor",
    # 清理函数
    "clear_container",
    "bootstrap_default_factories",
    "reset_bootstrap_for_tests",
]