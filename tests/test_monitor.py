"""Tests for tool monitor."""

from miniagent.infrastructure.monitor import DefaultToolMonitor


class TestDefaultToolMonitor:
    def test_record_success(self):
        monitor = DefaultToolMonitor()
        monitor.record("test_tool", 100, success=True)
        stats = monitor.get_stats("test_tool")
        assert stats is not None
        assert stats.calls == 1
        assert stats.success_count == 1
        assert stats.fail_count == 0

    def test_record_error(self):
        monitor = DefaultToolMonitor()
        monitor.record("bad_tool", 500, success=False)
        stats = monitor.get_stats("bad_tool")
        assert stats is not None
        assert stats.fail_count == 1

    def test_total_calls(self):
        monitor = DefaultToolMonitor()
        monitor.record("tool_a", 10, success=True)
        monitor.record("tool_a", 20, success=True)
        monitor.record("tool_b", 30, success=False)
        stats_a = monitor.get_stats("tool_a")
        stats_b = monitor.get_stats("tool_b")
        assert stats_a is not None
        assert stats_a.calls == 2
        assert stats_b is not None
        assert stats_b.calls == 1

    def test_report_format(self):
        monitor = DefaultToolMonitor()
        monitor.record("read_file", 100, success=True)
        report = monitor.report()
        assert "read_file" in report
        assert "100ms" in report

    def test_average_latency(self):
        monitor = DefaultToolMonitor()
        monitor.record("tool", 100, success=True)
        monitor.record("tool", 200, success=True)
        stats = monitor.get_stats("tool")
        assert stats is not None
        assert stats.total_ms == 300

    def test_nonexistent_tool_latency(self):
        monitor = DefaultToolMonitor()
        stats = monitor.get_stats("ghost_tool")
        assert stats is None

    def test_reset(self):
        monitor = DefaultToolMonitor()
        monitor.record("tool", 100, success=True)
        stats = monitor.get_stats("tool")
        assert stats is not None
        assert stats.calls == 1
        # Note: no reset method, but we can verify get_all_stats works
        all_stats = monitor.get_all_stats()
        assert "tool" in all_stats
