"""Mini Agent Python — 工具性能监控器

自动记录每次工具调用的耗时和成功/失败状态，
提供统计报告和单个工具的性能数据。

设计目标：
1. 零配置：Agent 运行时自动收集数据，无需手动埋点
2. 轻量：只记录必要的统计数据，不过度采集
3. 可见：通过 .stats 命令随时查看报告
"""

from __future__ import annotations

from miniagent.types.agent import ToolMonitorProtocol, ToolStats


class DefaultToolMonitor(ToolMonitorProtocol):
    """默认工具性能监控器实现

    内部使用字典存储每个工具的统计数据。
    每次 record() 调用时，累加调用次数和总耗时，并更新成功率。

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
        self._stats: dict[str, ToolStats] = {}

    def record(self, tool: str, duration_ms: int, success: bool) -> None:
        """记录一次工具调用

        更新逻辑：
        1. 获取或创建该工具的统计数据（首次调用时初始化为零）
        2. 调用次数 +1
        3. 累计耗时加上本次耗时
        4. 如果成功，success_count +1；否则 fail_count +1

        Args:
            tool: 工具名称
            duration_ms: 本次调用耗时（毫秒）
            success: 是否成功
        """
        if tool not in self._stats:
            self._stats[tool] = ToolStats()

        s = self._stats[tool]
        s.calls += 1
        s.total_ms += duration_ms
        if success:
            s.success_count += 1
        else:
            s.fail_count += 1

    def get_stats(self, tool: str) -> ToolStats | None:
        """获取单个工具的统计数据

        Args:
            tool: 工具名称

        Returns:
            统计数据，未找到返回 None
        """
        return self._stats.get(tool)

    def get_all_stats(self) -> dict[str, ToolStats]:
        """获取所有工具的统计数据

        Returns:
            所有工具的统计字典副本
        """
        return dict(self._stats)

    def report(self) -> str:
        """生成人类可读的统计报告

        格式示例：
            工具使用统计:

              read_file: 调用 5 次 | 平均 3ms | 成功率 100.0%
              exec_command: 调用 3 次 | 平均 1250ms | 成功率 66.7%
              get_time: 调用 1 次 | 平均 1ms | 成功率 100.0%

        Returns:
            格式化的报告字符串
        """
        if not self._stats:
            return "暂无工具使用数据"

        lines = ["工具使用统计:\n"]
        for name, s in self._stats.items():
            rate = (s.success_count / s.calls * 100) if s.calls > 0 else 0.0
            avg = s.total_ms // s.calls if s.calls > 0 else 0
            lines.append(f"  {name}: 调用 {s.calls} 次 | 平均 {avg}ms | 成功率 {rate:.1f}%")
        return "\n".join(lines)


__all__ = ["DefaultToolMonitor"]
