"""Tests for miniagent.types.error_prefix."""

from __future__ import annotations

import miniagent.types.error_prefix as error_prefix_module
from miniagent.types import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.types.error_prefix import (
    ERROR_PREFIX as DIRECT_ERROR_PREFIX,
    SUCCESS_PREFIX as DIRECT_SUCCESS_PREFIX,
    WARNING_PREFIX as DIRECT_WARNING_PREFIX,
)


def test_prefix_values() -> None:
    assert ERROR_PREFIX == "❌"
    assert WARNING_PREFIX == "⚠️"
    assert SUCCESS_PREFIX == "✅"


def test_reexported_from_types_package() -> None:
    assert DIRECT_ERROR_PREFIX == ERROR_PREFIX
    assert DIRECT_WARNING_PREFIX == WARNING_PREFIX
    assert DIRECT_SUCCESS_PREFIX == SUCCESS_PREFIX


def test_public_api() -> None:
    assert set(error_prefix_module.__all__) == {
        "ERROR_PREFIX",
        "WARNING_PREFIX",
        "SUCCESS_PREFIX",
    }
