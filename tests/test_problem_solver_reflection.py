"""测试结果反思评估 — problem_solver.py 的 reflect_on_result 函数。

覆盖反思评估的基本功能、结果解析、质量评分等。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.core.problem_solver import (
    ReflectionResult,
    reflect_on_result,
)


@pytest.mark.asyncio
async def test_reflect_on_result_acceptable():
    """测试可接受的反思结果。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"acceptable": true, "quality_score": 0.9, "issues": [], "suggestions": []}'
                    )
                )
            ]
        )
    )

    result = await reflect_on_result(
        user_input="帮我查找天气",
        reply="今天天气晴朗，温度25°C",
        client=mock_client,
    )

    assert result.acceptable is True
    assert result.quality_score == 0.9
    assert len(result.issues) == 0
    assert len(result.suggestions) == 0


@pytest.mark.asyncio
async def test_reflect_on_result_with_issues():
    """测试带问题的反思结果。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"acceptable": false, "quality_score": 0.4, "issues": ["信息不完整"], "suggestions": ["补充湿度信息"]}'
                    )
                )
            ]
        )
    )

    result = await reflect_on_result(
        user_input="查询北京天气",
        reply="天气晴",
        client=mock_client,
    )

    assert result.acceptable is False
    assert result.quality_score == 0.4
    assert len(result.issues) == 1
    assert "信息不完整" in result.issues
    assert len(result.suggestions) == 1
    assert "补充湿度信息" in result.suggestions


@pytest.mark.asyncio
async def test_reflect_on_result_with_thinking_callback():
    """测试带思考回调的反思评估。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(message=MagicMock(content='{"acceptable": true, "quality_score": 0.8}'))
            ]
        )
    )

    thinking_chunks = []

    async def mock_thinking(text, is_thinking, prefix):
        thinking_chunks.append((text, is_thinking, prefix))

    await reflect_on_result(
        user_input="测试输入",
        reply="测试回复",
        client=mock_client,
        on_thinking=mock_thinking,
    )

    # 应至少调用两次思考回调：评估开始和评估结束
    assert len(thinking_chunks) >= 2
    assert any("评估" in chunk[0] for chunk in thinking_chunks)


@pytest.mark.asyncio
async def test_reflect_on_result_default_values():
    """测试默认值处理。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{}'))]  # 空JSON
        )
    )

    result = await reflect_on_result(
        user_input="测试",
        reply="回复",
        client=mock_client,
    )

    # 应使用默认值
    assert result.acceptable is True  # 默认True
    assert result.quality_score == 0.5  # 默认0.5
    assert result.issues == []
    assert result.suggestions == []


@pytest.mark.asyncio
async def test_reflect_on_result_single_quality_score():
    """测试单个质量评分值。"""
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"acceptable": true, "quality_score": 0.85}'))]
        )
    )

    result = await reflect_on_result(
        user_input="test",
        reply="reply",
        client=mock_client,
    )

    assert 0.0 <= result.quality_score <= 1.0
    assert result.quality_score == 0.85


def test_reflection_result_dataclass():
    """测试反思结果数据类。"""
    result = ReflectionResult(
        acceptable=True,
        quality_score=0.8,
        issues=["问题1"],
        suggestions=["建议1", "建议2"],
    )

    assert result.acceptable is True
    assert result.quality_score == 0.8
    assert len(result.issues) == 1
    assert len(result.suggestions) == 2

    # 测试默认值
    result2 = ReflectionResult(acceptable=False, quality_score=0.3)
    assert result2.issues == []
    assert result2.suggestions == []
