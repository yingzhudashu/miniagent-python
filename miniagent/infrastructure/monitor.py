"""Mini Agent Python — 工具性能监控器

自动记录每次工具调用的耗时和成功/失败状态，
提供统计报告和单个工具的性能数据。

设计目标：
1. 零配置：Agent 运行时自动收集数据，无需手动埋点
2. 轻量：只记录必要的统计数据，不过度采集
3. 可见：通过 .stats 命令随时查看报告
4. 并发安全：多协程同时调用时保证数据一致性
"""

from __future__ import annotations

import threading

from miniagent.types.agent import ToolMonitorProtocol, ToolStats

_ERROR_SAMPLE_MAX = 20
_ERROR_SAMPLE_CHARS = 512


class _ThreadSafeToolStats:
    """线程安全的工具统计数据包装器。

    为ToolStats添加锁保护，保证多协程/多线程并发更新时的数据一致性。

    Attributes:
        _stats: 内部ToolStats数据对象
        _lock: 线程锁，保护字段更新
    """

    __slots__ = ("_stats", "_lock")

    def __init__(self) -> None:
        """创建线程安全的统计数据。"""
        self._stats = ToolStats()
        self._lock = threading.Lock()

    def record_call(self, duration_ms: int, success: bool, error_msg: str | None = None) -> None:
        """记录一次调用（线程安全）。

        Args:
            duration_ms: 本次调用耗时（毫秒）
            success: 是否成功
            error_msg: 错误信息（失败时可选）
        """
        with self._lock:
            self._stats.calls += 1
            self._stats.total_ms += duration_ms
            if success:
                self._stats.success_count += 1
            else:
                self._stats.fail_count += 1
                if error_msg and len(self._stats.errors) < _ERROR_SAMPLE_MAX:
                    self._stats.errors.append(error_msg[:_ERROR_SAMPLE_CHARS])

    def get_stats(self) -> ToolStats:
        """获取统计数据副本（线程安全）。

        Returns:
            统计数据副本，外部可安全访问
        """
        with self._lock:
            # 创建副本，避免外部修改影响内部状态
            return ToolStats(
                calls=self._stats.calls,
                total_ms=self._stats.total_ms,
                success_count=self._stats.success_count,
                fail_count=self._stats.fail_count,
                errors=list(self._stats.errors),  # 复制列表
            )


class DefaultToolMonitor(ToolMonitorProtocol):
    """默认工具性能监控器实现

    内部使用字典存储每个工具的统计数据。
    每次 record() 调用时，累加调用次数和总耗时，并更新成功率。

    并发安全：使用线程安全包装器，保证多协程调用时数据一致性。

    Example:
        monitor = DefaultToolMonitor()
        monitor.record("read_file", 150, success=True)
        monitor.record("exec_command", 2500, success=False)
        print(monitor.report())
    """

    def __init__(self) -> None:
        """创建工具性能监控器。

        初始化空的统计数据字典。每次 record() 调用时自动创建
        新工具的统计记录（零配置）。
        """
        self._stats: dict[str, _ThreadSafeToolStats] = {}
        # 保护字典本身的全局锁（用于新工具创建）
        self._dict_lock = threading.Lock()

    def record(
        self,
        tool: str,
        duration_ms: int,
        success: bool,
        *,
        error: str | None = None,
    ) -> None:
        """记录一次工具调用（线程安全）

        更新逻辑：
        1. 获取或创建该工具的统计数据（首次调用时初始化为零）
        2. 调用次数 +1
        3. 累计耗时加上本次耗时
        4. 如果成功，success_count +1；否则 fail_count +1

        Args:
            tool: 工具名称
            duration_ms: 本次调用耗时（毫秒）
            success: 是否成功
            error: 失败时的错误摘要（可选）
        """
        # 获取或创建统计对象（需锁保护字典）
        with self._dict_lock:
            if tool not in self._stats:
                self._stats[tool] = _ThreadSafeToolStats()
            ts = self._stats[tool]

        # 记录调用（对象内部有锁保护）
        ts.record_call(duration_ms, success, error_msg=error)

    def get_stats(self, tool: str) -> ToolStats | None:
        """获取单个工具的统计数据（线程安全）

        Args:
            tool: 工具名称

        Returns:
            统计数据副本，未找到返回 None
        """
        with self._dict_lock:
            ts = self._stats.get(tool)
        if ts:
            return ts.get_stats()
        return None

    def get_all_stats(self) -> dict[str, ToolStats]:
        """获取所有工具的统计数据（线程安全）

        Returns:
            所有工具的统计字典副本
        """
        with self._dict_lock:
            return {name: ts.get_stats() for name, ts in self._stats.items()}

    def report(self) -> str:
        """生成人类可读的统计报告（线程安全）

        格式示例：
            工具使用统计:

              read_file: 调用 5 次 | 平均 3ms | 成功率 100.0%
              exec_command: 调用 3 次 | 平均 1250ms | 成功率 66.7%
              get_time: 调用 1 次 | 平均 1ms | 成功率 100.0%

        Returns:
            格式化的报告字符串
        """
        all_stats = self.get_all_stats()
        if not all_stats:
            return "暂无工具使用数据"

        lines = ["工具使用统计:\n"]
        for name, s in all_stats.items():
            rate = (s.success_count / s.calls * 100) if s.calls > 0 else 0.0
            avg = s.total_ms // s.calls if s.calls > 0 else 0
            lines.append(f"  {name}: 调用 {s.calls} 次 | 平均 {avg}ms | 成功率 {rate:.1f}%")
        return "\n".join(lines)


__all__ = ["DefaultToolMonitor"]
