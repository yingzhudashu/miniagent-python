"""Tests for Async PATCH - 飞书异步 PATCH 函数测试。

测试目标：
- patch_im_message_async：异步 PATCH 更新消息
- _post_interactive_message_async：异步发送交互消息
- _patch_interactive_thinking_message_async：异步 PATCH 思考卡片
- _create_interactive_thinking_message_async：异步创建思考卡片
- 智能节流逻辑：_is_important_content_for_immediate_patch
- 动态预算调整：_adjust_patch_budget_dynamically
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ============================================================================
# 异步 PATCH 函数测试
# ============================================================================


class TestPatchImMessageAsync:
    """patch_im_message_async 异步 PATCH 测试。"""

    @pytest.mark.asyncio
    async def test_patch_im_message_async_success(self) -> None:
        """异步 PATCH 成功场景。"""
        from miniagent.feishu.im_send import patch_im_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        # Mock 成功响应
        with patch("miniagent.feishu.im_send.patch_im_message") as mock_patch:
            mock_patch.return_value = (True, None)

            ok, err = await patch_im_message_async(
                cfg,
                message_id="om_test",
                content_json='{"test": "content"}',
                timeout=5.0,
            )

            assert ok is True
            assert err is None
            mock_patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_patch_im_message_async_failure(self) -> None:
        """异步 PATCH 失败场景。"""
        from miniagent.feishu.im_send import patch_im_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        with patch("miniagent.feishu.im_send.patch_im_message") as mock_patch:
            mock_patch.return_value = (False, "API error")

            ok, err = await patch_im_message_async(
                cfg,
                message_id="om_test",
                content_json='{"test": "content"}',
            )

            assert ok is False
            assert err == "API error"

    @pytest.mark.asyncio
    async def test_patch_im_message_async_timeout(self) -> None:
        """异步 PATCH 超时场景。"""
        from miniagent.feishu.im_send import patch_im_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        # Mock 阻塞调用
        def slow_patch() -> tuple[bool, str | None]:
            import time
            time.sleep(5)  # 模拟长时间阻塞
            return (True, None)

        with patch("miniagent.feishu.im_send.patch_im_message", side_effect=slow_patch):
            ok, err = await patch_im_message_async(
                cfg,
                message_id="om_test",
                content_json='{"test": "content"}',
                timeout=0.1,  # 短超时
            )

            assert ok is False
            assert "timeout" in (err or "")


class TestPostInteractiveMessageAsync:
    """_post_interactive_message_async 异步发送交互消息测试。"""

    @pytest.mark.asyncio
    async def test_post_interactive_success(self) -> None:
        """异步发送交互消息成功。"""
        from miniagent.feishu.poll_server import _post_interactive_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        with patch("miniagent.feishu.im_send.post_im_message_async") as mock_post:
            mock_post.return_value = (True, "om_new_message", None)

            ok, mid = await _post_interactive_message_async(
                cfg,
                receive_id="oc_test",
                card_json='{"card": "test"}',
            )

            assert ok is True
            assert mid == "om_new_message"

    @pytest.mark.asyncio
    async def test_post_interactive_failure(self) -> None:
        """异步发送交互消息失败。"""
        from miniagent.feishu.poll_server import _post_interactive_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        with patch("miniagent.feishu.im_send.post_im_message_async") as mock_post:
            mock_post.return_value = (False, None, "send failed")

            ok, mid = await _post_interactive_message_async(
                cfg,
                receive_id="oc_test",
                card_json='{"card": "test"}',
            )

            assert ok is False
            assert mid is None


class TestPatchInteractiveThinkingMessageAsync:
    """_patch_interactive_thinking_message_async 异步 PATCH 思考卡片测试。"""

    @pytest.mark.asyncio
    async def test_patch_thinking_success(self) -> None:
        """异步 PATCH 思考卡片成功。"""
        from miniagent.feishu.poll_server import _patch_interactive_thinking_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        with patch("miniagent.feishu.im_send.patch_im_message_async") as mock_patch:
            mock_patch.return_value = (True, None)

            ok = await _patch_interactive_thinking_message_async(
                cfg,
                message_id="om_thinking",
                card_json='{"thinking": "test"}',
            )

            assert ok is True

    @pytest.mark.asyncio
    async def test_patch_thinking_failure(self) -> None:
        """异步 PATCH 思考卡片失败。"""
        from miniagent.feishu.poll_server import _patch_interactive_thinking_message_async
        from miniagent.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="test_app", app_secret="test_secret", verification_token="test_token")

        with patch("miniagent.feishu.im_send.patch_im_message_async") as mock_patch:
            mock_patch.return_value = (False, "patch failed")

            ok = await _patch_interactive_thinking_message_async(
                cfg,
                message_id="om_thinking",
                card_json='{"thinking": "test"}',
            )

            assert ok is False


# ============================================================================
# 智能节流测试
# ============================================================================


class TestSmartThrottling:
    """智能节流逻辑测试。"""

    def test_important_content_code_block_start(self) -> None:
        """代码块开始应该立即 PATCH。"""
        from miniagent.feishu.poll_server import _is_important_content_for_immediate_patch

        # 未闭合的代码块
        text = "Here is some code:\n```python\ndef hello():"
        assert _is_important_content_for_immediate_patch(text) is True

        # 已闭合的代码块（不紧急）
        text = "```python\ndef hello():\n```"
        assert _is_important_content_for_immediate_patch(text) is False

    def test_important_content_heading(self) -> None:
        """Markdown 标题应该立即 PATCH。"""
        from miniagent.feishu.poll_server import _is_important_content_for_immediate_patch

        assert _is_important_content_for_immediate_patch("# Title") is True
        assert _is_important_content_for_immediate_patch("## Subtitle") is True
        assert _is_important_content_for_immediate_patch("### Section") is True
        assert _is_important_content_for_immediate_patch("Normal text") is False

    def test_important_content_table(self) -> None:
        """表格行应该立即 PATCH。"""
        from miniagent.feishu.poll_server import _is_important_content_for_immediate_patch

        assert _is_important_content_for_immediate_patch("| Col1 | Col2 |") is True
        assert _is_important_content_for_immediate_patch("|---|---|") is True
        assert _is_important_content_for_immediate_patch("No table here") is False

    def test_important_content_list(self) -> None:
        """列表开始应该立即 PATCH。"""
        from miniagent.feishu.poll_server import _is_important_content_for_immediate_patch

        assert _is_important_content_for_immediate_patch("- First item") is True
        assert _is_important_content_for_immediate_patch("* Another item") is True
        assert _is_important_content_for_immediate_patch("1. Numbered item") is True
        assert _is_important_content_for_immediate_patch("Not a list") is False

    def test_important_content_disabled(self) -> None:
        """禁用重要内容立即 PATCH 时返回 False。"""
        from miniagent.feishu.poll_server import _is_important_content_for_immediate_patch

        with patch("miniagent.feishu.poll_server.FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE", False):
            text = "```python\ndef hello():"
            assert _is_important_content_for_immediate_patch(text) is False


class TestDynamicBudgetAdjustment:
    """动态预算调整测试。"""

    def test_budget_adjustment_short_text(self) -> None:
        """短文本不调整预算。"""
        from miniagent.feishu.poll_server import (
            FEISHU_THINKING_PATCH_BUDGET,
            _adjust_patch_budget_dynamically,
        )

        budget = _adjust_patch_budget_dynamically(1000, FEISHU_THINKING_PATCH_BUDGET)
        assert budget == FEISHU_THINKING_PATCH_BUDGET

    def test_budget_adjustment_medium_text(self) -> None:
        """中等长度文本增加预算。"""
        from miniagent.feishu.poll_server import (
            FEISHU_THINKING_PATCH_BUDGET,
            _adjust_patch_budget_dynamically,
        )

        # 使用当前配置的预算值进行测试
        base_budget = FEISHU_THINKING_PATCH_BUDGET
        budget = _adjust_patch_budget_dynamically(6000, base_budget)
        assert budget == base_budget + 20

    def test_budget_adjustment_long_text(self) -> None:
        """长文本大幅增加预算。"""
        from miniagent.feishu.poll_server import (
            FEISHU_THINKING_PATCH_BUDGET,
            _adjust_patch_budget_dynamically,
        )

        # 使用当前配置的预算值进行测试
        base_budget = FEISHU_THINKING_PATCH_BUDGET
        budget = _adjust_patch_budget_dynamically(12000, base_budget)
        assert budget == base_budget + 40

    def test_budget_adjustment_preserves_higher_budget(self) -> None:
        """已有更高预算时保持不变。"""
        from miniagent.feishu.poll_server import (
            FEISHU_THINKING_PATCH_BUDGET,
            _adjust_patch_budget_dynamically,
        )

        # 使用当前配置的预算值进行测试
        base_budget = FEISHU_THINKING_PATCH_BUDGET
        # 当前预算已经很高，不需要增加
        budget = _adjust_patch_budget_dynamically(6000, base_budget + 50)
        assert budget == base_budget + 50


__all__ = [
    "TestPatchImMessageAsync",
    "TestPostInteractiveMessageAsync",
    "TestPatchInteractiveThinkingMessageAsync",
    "TestSmartThrottling",
    "TestDynamicBudgetAdjustment",
]
