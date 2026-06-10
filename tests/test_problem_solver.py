"""Problem Solver 反思评估测试

测试 miniagent/core/problem_solver.py 的反思评估功能。
"""

from unittest.mock import AsyncMock, patch

import pytest

from miniagent.core.problem_solver import ReflectionResult, reflect_on_result


class TestReflectionResult:
    """ReflectionResult 数据类测试"""

    def test_reflection_result_creation_default(self):
        """测试默认值创建"""
        result = ReflectionResult(
            acceptable=True,
            quality_score=0.8,
        )
        assert result.acceptable is True
        assert result.quality_score == 0.8
        assert result.issues == []
        assert result.suggestions == []

    def test_reflection_result_creation_with_values(self):
        """测试带值创建"""
        result = ReflectionResult(
            acceptable=False,
            quality_score=0.3,
            issues=["问题1", "问题2"],
            suggestions=["建议1", "建议2"],
        )
        assert result.acceptable is False
        assert result.quality_score == 0.3
        assert len(result.issues) == 2
        assert len(result.suggestions) == 2

    def test_reflection_result_quality_score_range(self):
        """测试质量评分范围"""
        # 正常范围
        result1 = ReflectionResult(acceptable=True, quality_score=0.0)
        result2 = ReflectionResult(acceptable=True, quality_score=1.0)
        assert result1.quality_score == 0.0
        assert result2.quality_score == 1.0


@pytest.mark.asyncio
class TestReflectOnResult:
    """reflect_on_result 函数测试"""

    async def test_reflect_on_result_basic(self):
        """测试基本反思评估"""
        # Mock LLM JSON 响应
        mock_response = {
            "acceptable": True,
            "quality_score": 0.85,
            "issues": [],
            "suggestions": ["回答清晰"],
        }

        with patch("miniagent.core.problem_solver.llm_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            result = await reflect_on_result(
                user_input="帮我写一个Python函数",
                reply="已为你编写函数代码...",
                client=None,
            )

            assert result.acceptable is True
            assert result.quality_score == 0.85
            assert len(result.suggestions) == 1

    async def test_reflect_on_result_with_issues(self):
        """测试发现问题的反思"""
        mock_response = {
            "acceptable": False,
            "quality_score": 0.4,
            "issues": ["缺少错误处理", "代码不够简洁"],
            "suggestions": ["添加异常捕获", "简化逻辑"],
        }

        with patch("miniagent.core.problem_solver.llm_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            result = await reflect_on_result(
                user_input="优化这段代码",
                reply="原代码：...",
                client=None,
            )

            assert result.acceptable is False
            assert result.quality_score == 0.4
            assert len(result.issues) == 2
            assert len(result.suggestions) == 2

    async def test_reflect_on_result_with_thinking_callback(self):
        """测试带思考回调的反思"""
        mock_response = {
            "acceptable": True,
            "quality_score": 0.9,
            "issues": [],
            "suggestions": [],
        }

        mock_on_thinking = AsyncMock()

        with patch("miniagent.core.problem_solver.llm_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            await reflect_on_result(
                user_input="测试输入",
                reply="测试回复",
                client=None,
                on_thinking=mock_on_thinking,
            )

            # 验证回调被调用
            assert mock_on_thinking.call_count >= 2  # 至少2次（开始和结束）

    async def test_reflect_on_result_with_knowledge_base(self):
        """测试带知识库检索的反思"""
        mock_response = {
            "acceptable": True,
            "quality_score": 0.75,
            "issues": [],
            "suggestions": ["参考知识库标准"],
        }

        mock_kb_context = "知识库参考：最佳实践文档"

        with patch("miniagent.core.problem_solver.llm_json", new_callable=AsyncMock) as mock_llm:
            with patch("miniagent.knowledge.retrieve_knowledge_context") as mock_kb:
                mock_llm.return_value = mock_response
                mock_kb.return_value = mock_kb_context

                await reflect_on_result(
                    user_input="如何编写高质量代码",
                    reply="建议...",
                    client=None,
                )

                # 验证知识库检索被调用
                mock_kb.assert_called_once()

    async def test_reflect_on_result_default_values(self):
        """测试LLM响应缺失字段时的默认值"""
        mock_response = {}  # 空响应

        with patch("miniagent.core.problem_solver.llm_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response

            result = await reflect_on_result(
                user_input="输入",
                reply="回复",
                client=None,
            )

            # 验证默认值
            assert result.acceptable is True  # 默认 True
            assert result.quality_score == 0.5  # 默认 0.5
            assert result.issues == []
            assert result.suggestions == []


@pytest.mark.asyncio
class TestReflectOnResultIntegration:
    """反思评估集成测试"""

    async def test_reflect_on_result_full_flow(self):
        """测试完整反思流程"""
        mock_response = {
            "acceptable": False,
            "quality_score": 0.6,
            "issues": ["缺少详细说明"],
            "suggestions": ["补充示例代码"],
        }

        mock_on_thinking = AsyncMock()

        with patch("miniagent.core.problem_solver.llm_json", new_callable=AsyncMock) as mock_llm:
            with patch("miniagent.knowledge.retrieve_knowledge_context") as mock_kb:
                mock_llm.return_value = mock_response
                mock_kb.return_value = ""

                result = await reflect_on_result(
                    user_input="写一个排序算法",
                    reply="这是一个冒泡排序实现...",
                    client=None,
                    on_thinking=mock_on_thinking,
                )

                # 验证完整结果
                assert isinstance(result, ReflectionResult)
                assert result.acceptable is False
                assert 0 <= result.quality_score <= 1
                assert isinstance(result.issues, list)
                assert isinstance(result.suggestions, list)


class TestReflectionResultDataClass:
    """ReflectionResult 数据类特性测试"""

    def test_reflection_result_equality(self):
        """测试数据类相等性"""
        result1 = ReflectionResult(acceptable=True, quality_score=0.8)
        result2 = ReflectionResult(acceptable=True, quality_score=0.8)
        # 数据类自动实现 __eq__
        assert result1 == result2

    def test_reflection_result_inequality(self):
        """测试数据类不等性"""
        result1 = ReflectionResult(acceptable=True, quality_score=0.8)
        result2 = ReflectionResult(acceptable=False, quality_score=0.8)
        assert result1 != result2

    def test_reflection_result_field_defaults(self):
        """测试字段默认值"""
        result = ReflectionResult(acceptable=True, quality_score=1.0)
        # 验证默认工厂字段
        assert result.issues == []
        assert result.suggestions == []
        # 确保是独立实例
        result.issues.append("test")
        result2 = ReflectionResult(acceptable=True, quality_score=1.0)
        assert result2.issues == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])