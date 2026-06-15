"""Tests for miniagent.utils.error_handling."""

from __future__ import annotations

import asyncio
import logging

import pytest

from miniagent.utils import error_handling
from miniagent.utils.error_handling import (
    _log_failure,
    log_exception,
    safe_execute,
    safe_execute_sync,
)


@pytest.fixture
def caplog_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route module logging through caplog instead of infrastructure stderr handlers."""

    def _test_get_logger(name: str) -> logging.Logger:
        logger = logging.getLogger(f"test.error_handling.{name}")
        logger.handlers.clear()
        logger.propagate = True
        return logger

    monkeypatch.setattr(error_handling, "_get_logger", _test_get_logger)


class TestLogFailure:
    """Tests for _log_failure."""

    def test_logs_at_requested_level(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("test_error_handling.level")
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            _log_failure(logger, "error", "boom", include_trace=False)
        assert any(r.levelname == "ERROR" and "boom" in r.message for r in caplog.records)

    def test_invalid_level_falls_back_to_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("test_error_handling.invalid_level")
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            _log_failure(logger, "not_a_level", "fallback", include_trace=False)
        assert any(r.levelname == "WARNING" and "fallback" in r.message for r in caplog.records)

    def test_include_trace_attaches_exc_info(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("test_error_handling.trace")
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            try:
                raise RuntimeError("trace me")
            except RuntimeError:
                _log_failure(logger, "warning", "with trace", include_trace=True)
        assert any(r.exc_info is not None for r in caplog.records)


class TestSafeExecute:
    """Tests for safe_execute."""

    def test_sync_success(self) -> None:
        @safe_execute(default_return=0)
        def ok() -> int:
            return 99

        assert ok() == 99

    def test_sync_returns_default_on_failure(
        self, caplog: pytest.LogCaptureFixture, caplog_logger: None
    ) -> None:
        @safe_execute(default_return=42, log_level="warning")
        def fail() -> int:
            raise ValueError("sync err")

        with caplog.at_level(logging.WARNING):
            assert fail() == 42
        assert any("sync err" in r.message for r in caplog.records)

    def test_sync_reraise_preserves_original_exception(self) -> None:
        @safe_execute(reraise=True)
        def fail() -> None:
            raise KeyError("missing")

        with pytest.raises(KeyError, match="missing"):
            fail()

    def test_async_returns_default_on_failure(self) -> None:
        @safe_execute(default_return="async_default")
        async def fail_async() -> str:
            raise RuntimeError("async err")

        assert asyncio.run(fail_async()) == "async_default"

    def test_async_success(self) -> None:
        @safe_execute(default_return=None)
        async def ok_async() -> int:
            return 7

        assert asyncio.run(ok_async()) == 7

    def test_log_exception_trace(
        self, caplog: pytest.LogCaptureFixture, caplog_logger: None
    ) -> None:
        @safe_execute(default_return=None, log_exception_trace=True)
        def fail() -> None:
            raise OSError("disk")

        with caplog.at_level(logging.WARNING):
            assert fail() is None
        assert any(r.exc_info is not None for r in caplog.records)


class TestSafeExecuteSync:
    """Tests for safe_execute_sync."""

    def test_returns_default_on_failure(self) -> None:
        @safe_execute_sync(default_return=[])
        def fail() -> list[str]:
            raise OSError("io")

        assert fail() == []

    def test_reraise(self) -> None:
        @safe_execute_sync(reraise=True)
        def fail() -> None:
            raise TypeError("bad type")

        with pytest.raises(TypeError, match="bad type"):
            fail()


class TestLogException:
    """Tests for log_exception."""

    def test_logs_manual_exception(
        self, caplog: pytest.LogCaptureFixture, caplog_logger: None
    ) -> None:
        exc = ValueError("manual")
        with caplog.at_level(logging.DEBUG):
            log_exception("manual_fn", exc, __name__, level="error", include_trace=False)
        assert any(
            r.levelname == "ERROR" and "manual" in r.message for r in caplog.records
        )
