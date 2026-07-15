"""Tests for miniagent.agent.thinking_presets."""

import pytest

from miniagent.agent.thinking_presets import (
    THINKING_LEVEL_PRESETS,
    map_business_depth,
    map_thinking_level_to_model,
)


class TestMapThinkingLevelToModel:
    """map_thinking_level_to_model 将档位映射为 (level, budget)。"""

    def test_low(self):
        assert map_thinking_level_to_model("low") == ("light", 1024)

    def test_medium(self):
        assert map_thinking_level_to_model("medium") == ("medium", 8192)

    def test_high(self):
        assert map_thinking_level_to_model("high") == ("heavy", 81920)

    def test_case_insensitive(self):
        assert map_thinking_level_to_model("LOW") == ("light", 1024)
        assert map_thinking_level_to_model("Medium") == ("medium", 8192)

    def test_unknown_defaults_to_medium(self):
        assert map_thinking_level_to_model("unknown") == ("medium", 8192)

    def test_none_defaults_to_medium(self):
        assert map_thinking_level_to_model(None) == ("medium", 8192)

    def test_empty_string_defaults_to_medium(self):
        assert map_thinking_level_to_model("") == ("medium", 8192)

    def test_chinese_not_supported_defaults_to_medium(self):
        assert map_thinking_level_to_model("低") == ("medium", 8192)
        assert map_thinking_level_to_model("复杂") == ("medium", 8192)


class TestMapBusinessDepth:
    """map_business_depth 将规划/步骤 thinkingLevel 映射为 (level, budget)。"""

    @pytest.mark.parametrize(
        "inputs,expected",
        [
            (["simple", "low", "轻", "低"], ("light", 1024)),
            (["normal", "medium", "中", "一般"], ("medium", 8192)),
            (["high", "complex", "重", "高", "复杂"], ("heavy", 81920)),
        ],
    )
    def test_known_levels(self, inputs, expected):
        for inp in inputs:
            assert map_business_depth(inp) == expected

    def test_none_defaults_to_medium(self):
        assert map_business_depth(None) == ("medium", 8192)

    def test_empty_string_defaults_to_medium(self):
        assert map_business_depth("") == ("medium", 8192)

    def test_unknown_defaults_to_medium(self):
        assert map_business_depth("foobar") == ("medium", 8192)

    def test_whitespace_stripped(self):
        assert map_business_depth("  LOW  ") == ("light", 1024)

    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("light", ("light", 1024)),
            ("heavy", ("heavy", 81920)),
            ("  LIGHT  ", ("light", 1024)),
        ],
    )
    def test_model_tier_passthrough(self, inp, expected):
        assert map_business_depth(inp) == expected


class TestThinkingLevelPresetsConstant:
    """THINKING_LEVEL_PRESETS 常量结构验证。"""

    def test_has_all_keys(self):
        assert set(THINKING_LEVEL_PRESETS.keys()) == {"low", "medium", "high"}

    def test_values_are_tuples(self):
        for val in THINKING_LEVEL_PRESETS.values():
            assert isinstance(val, tuple)
            assert len(val) == 2
            assert isinstance(val[0], str)
            assert isinstance(val[1], int)
