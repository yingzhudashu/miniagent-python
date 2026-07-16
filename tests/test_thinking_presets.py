"""Tests for miniagent.agent.thinking_presets."""

import pytest

from miniagent.agent.thinking_presets import (
    THINKING_LEVEL_PRESETS,
    map_business_depth,
    map_thinking_level_to_model,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("low", ("light", 1024)),
        ("medium", ("medium", 8192)),
        ("high", ("heavy", 81920)),
        ("LOW", ("light", 1024)),
        ("Medium", ("medium", 8192)),
        ("unknown", ("medium", 8192)),
        (None, ("medium", 8192)),
        ("", ("medium", 8192)),
        ("\u4f4e", ("medium", 8192)),
        ("\u590d\u6742", ("medium", 8192)),
    ],
    ids=[
        "low",
        "medium",
        "high",
        "uppercase-low",
        "mixed-case-medium",
        "unknown",
        "none",
        "empty",
        "localized-low",
        "localized-complex",
    ],
)
def test_map_thinking_level_to_model(
    value: str | None,
    expected: tuple[str, int],
) -> None:
    assert map_thinking_level_to_model(value) == expected


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        (["simple", "low", "\u8f7b", "\u4f4e"], ("light", 1024)),
        (["normal", "medium", "\u4e2d", "\u4e00\u822c"], ("medium", 8192)),
        (["high", "complex", "\u91cd", "\u9ad8", "\u590d\u6742"], ("heavy", 81920)),
    ],
    ids=["light", "medium", "heavy"],
)
def test_map_business_depth_known_levels(
    inputs: list[str],
    expected: tuple[str, int],
) -> None:
    for value in inputs:
        assert map_business_depth(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, "", "foobar"],
    ids=["none", "empty", "unknown"],
)
def test_map_business_depth_defaults_to_medium(value: str | None) -> None:
    assert map_business_depth(value) == ("medium", 8192)


def test_map_business_depth_strips_whitespace() -> None:
    assert map_business_depth("  LOW  ") == ("light", 1024)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("light", ("light", 1024)),
        ("heavy", ("heavy", 81920)),
        ("  LIGHT  ", ("light", 1024)),
    ],
    ids=["light", "heavy", "normalized-light"],
)
def test_map_business_depth_model_tier_passthrough(
    value: str,
    expected: tuple[str, int],
) -> None:
    assert map_business_depth(value) == expected


def test_thinking_level_presets_has_all_keys() -> None:
    assert set(THINKING_LEVEL_PRESETS) == {"low", "medium", "high"}


def test_thinking_level_presets_values_are_typed_tuples() -> None:
    for value in THINKING_LEVEL_PRESETS.values():
        assert isinstance(value, tuple)
        assert len(value) == 2
        assert isinstance(value[0], str)
        assert isinstance(value[1], int)
