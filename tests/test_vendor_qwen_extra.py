"""Tests for miniagent/core/vendor/qwen_extra.py."""


from miniagent.core.vendor.qwen_extra import build_thinking_extra_body


class TestBuildThinkingExtraBody:
    """Tests for build_thinking_extra_body."""

    def test_non_qwen_endpoint_empty(self):
        """Non-Qwen endpoints return merged dict without thinking fields."""
        result = build_thinking_extra_body(
            "https://api.openai.com/v1",
            "medium",
            1024,
        )
        assert result == {}

    def test_dashscope_endpoint_with_thinking(self):
        """DashScope endpoint gets thinking fields."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "medium",
            1024,
        )
        assert result.get("enable_thinking") is True
        assert result.get("thinking_budget") == 1024

    def test_dashscope_endpoint_zero_budget(self):
        """Zero budget disables thinking."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "medium",
            0,
        )
        assert result.get("enable_thinking") is False

    def test_disabled_thinking_level(self):
        """disabled level disables thinking."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "disabled",
            1024,
        )
        assert result.get("enable_thinking") is False

    def test_none_thinking_level(self):
        """none level disables thinking (ModelConfig semantics)."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "none",
            1024,
        )
        assert result.get("enable_thinking") is False

    def test_non_qwen_endpoint_with_user_extra_body(self):
        """Non-Qwen endpoints pass through user extra_body unchanged."""
        result = build_thinking_extra_body(
            "https://api.openai.com/v1",
            "medium",
            1024,
            model_overrides_extra={"extra_body": {"custom_field": "value"}},
        )
        assert result == {"custom_field": "value"}

    def test_zero_budget_overrides_user_enable_thinking(self):
        """Zero budget forces thinking off even if user extra_body enabled it."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "medium",
            0,
            model_overrides_extra={"extra_body": {"enable_thinking": True}},
        )
        assert result.get("enable_thinking") is False

    def test_aliyuncs_endpoint(self):
        """aliyuncs.com endpoint recognized."""
        result = build_thinking_extra_body(
            "https://openai-api.aliyuncs.com/v1",
            "low",
            512,
        )
        assert "enable_thinking" in result

    def test_coding_dashscope_endpoint(self):
        """coding.dashscope endpoint recognized."""
        result = build_thinking_extra_body(
            "https://coding.dashscope.aliyuncs.com/v1",
            "high",
            2048,
        )
        assert result.get("enable_thinking") is True

    def test_model_overrides_extra_body_merged(self):
        """User extra_body merged with thinking fields."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "medium",
            1024,
            model_overrides_extra={"extra_body": {"custom_field": "value"}},
        )
        assert result.get("custom_field") == "value"
        assert result.get("enable_thinking") is True

    def test_model_overrides_empty(self):
        """Empty model_overrides_extra handled."""
        result = build_thinking_extra_body(
            "https://api.openai.com/v1",
            "medium",
            1024,
            model_overrides_extra={},
        )
        assert result == {}

    def test_model_overrides_none(self):
        """None model_overrides_extra handled."""
        result = build_thinking_extra_body(
            "https://api.openai.com/v1",
            "medium",
            1024,
            model_overrides_extra=None,
        )
        assert result == {}

    def test_empty_base_url(self):
        """Empty base_url returns empty dict."""
        result = build_thinking_extra_body(
            "",
            "medium",
            1024,
        )
        assert result == {}

    def test_none_base_url(self):
        """None base_url returns empty dict."""
        result = build_thinking_extra_body(
            None,
            "medium",
            1024,
        )
        assert result == {}

    def test_case_insensitive_base_url(self):
        """Base URL detection is case insensitive."""
        result = build_thinking_extra_body(
            "HTTPS://DASHSCOPE.ALIYUNCS.COM/V1",
            "medium",
            1024,
        )
        assert result.get("enable_thinking") is True

    def test_thinking_level_variants(self):
        """Various thinking_level values handled."""
        for level in ["low", "medium", "high", "light"]:
            result = build_thinking_extra_body(
                "https://dashscope.aliyuncs.com/v1",
                level,
                512,
            )
            assert "enable_thinking" in result

    def test_thinking_budget_negative(self):
        """Negative budget treated as zero."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "medium",
            -100,
        )
        # Negative budget should not enable thinking
        assert result.get("enable_thinking") is False

    def test_user_extra_body_overrides_default(self):
        """User extra_body can override defaults."""
        result = build_thinking_extra_body(
            "https://dashscope.aliyuncs.com/v1",
            "medium",
            1024,
            model_overrides_extra={"extra_body": {"thinking_budget": 2048}},
        )
        # User value merged, then thinking_budget set (setdefault won't override)
        assert result.get("thinking_budget") == 2048