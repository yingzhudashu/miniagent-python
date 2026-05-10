"""Tests for loop detector."""

from miniagent.infrastructure.loop_detector import LoopDetector


class TestLoopDetector:
    def test_no_loop_on_first_call(self):
        detector = LoopDetector()
        detector.record("read_file", {"path": "test.txt"}, "success")
        result = detector.check("read_file", {"path": "test.txt"})
        assert result.level == "none"

    def test_detect_repeated_calls(self):
        detector = LoopDetector()
        # Record same action 8 times (default critical threshold)
        for _ in range(8):
            detector.record("read_file", {"path": "test.txt"}, "success")
        result = detector.check("read_file", {"path": "test.txt"})
        assert result.level == "critical"

    def test_warning_threshold(self):
        detector = LoopDetector()
        # Record 5 times (default warning threshold)
        for _ in range(5):
            detector.record("read_file", {"path": "test.txt"}, "success")
        result = detector.check("read_file", {"path": "test.txt"})
        assert result.level == "warning"

    def test_different_args_no_loop(self):
        detector = LoopDetector()
        detector.record("read_file", {"path": "a.txt"}, "ok")
        detector.record("read_file", {"path": "b.txt"}, "ok")
        result = detector.check("read_file", {"path": "c.txt"})
        assert result.level == "none"

    def test_different_action_no_loop(self):
        detector = LoopDetector()
        detector.record("read_file", {"path": "test.txt"}, "ok")
        detector.record("write_file", {"path": "test.txt"}, "ok")
        result = detector.check("read_file", {"path": "test.txt"})
        assert result.level == "none"

    def test_clear(self):
        detector = LoopDetector()
        for _ in range(10):
            detector.record("read_file", {"path": "test.txt"}, "ok")
        detector.clear()
        result = detector.check("read_file", {"path": "test.txt"})
        assert result.level == "none"

    def test_get_stats(self):
        detector = LoopDetector()
        detector.record("read_file", {"path": "test.txt"}, "ok")
        stats = detector.get_stats()
        assert stats["total_calls"] == 1
        assert stats["enabled"] is True
