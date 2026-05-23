"""Problem Solver 单元测试（Mock LLM）。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from miniagent.core.problem_solver import ProblemAnalysis, ProblemSolver, ReflectionResult


class TestProblemSolver:
    """ProblemSolver 行为测试（Mock LLM）。"""

    def _make_mock_client(self, json_response: dict) -> MagicMock:
        """构造 Mock LLM client。"""
        mock_choice = MagicMock()
        mock_choice.message.content = __import__("json").dumps(json_response)
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        mock_completion = MagicMock()
        mock_completion.create = MagicMock(return_value=asyncio.coroutine(lambda **kw: mock_response)())

        mock_client = MagicMock()
        mock_client.chat.completions = mock_completion
        mock_client.chat.completions.create = MagicMock(
            return_value=asyncio.coroutine(lambda **kw: mock_response)()
        )
        return mock_client

    def test_problem_analysis_dataclass(self) -> None:
        """ProblemAnalysis 数据结构测试。"""
        analysis = ProblemAnalysis(
            knowns=["user wants weather info"],
            unknowns=["location"],
            constraints=["must be concise"],
            goal="Get weather report",
        )
        assert len(analysis.knowns) == 1
        assert len(analysis.unknowns) == 1
        assert analysis.goal == "Get weather report"

    def test_reflection_result_dataclass(self) -> None:
        """ReflectionResult 数据结构测试。"""
        reflection = ReflectionResult(
            acceptable=True,
            quality_score=0.8,
            issues=[],
            suggestions=["add more detail"],
        )
        assert reflection.acceptable is True
        assert reflection.quality_score == 0.8

    @pytest.mark.asyncio
    async def test_solve_returns_reply_and_reflection(self) -> None:
        """solve() 应返回 (reply, reflection) 元组。"""
        mock_registry = MagicMock()
        mock_registry.list.return_value = []
        mock_registry.get_all.return_value = {}
        mock_registry.get_schemas.return_value = []
        mock_client = MagicMock()
        solver = ProblemSolver(max_iterations=0)

        async def mock_llm(*args, **kwargs):
            return {}  # 空响应（问题分析会 fallback）

        async def mock_plan_fn(*args, **kwargs):
            return MagicMock(summary="test plan")

        async def mock_exec_fn(*args, **kwargs):
            return "test reply"

        with patch("miniagent.core.problem_solver.llm_json", side_effect=mock_llm), \
             patch("miniagent.core.problem_solver.generate_plan", side_effect=mock_plan_fn), \
             patch("miniagent.core.problem_solver.execute_plan", side_effect=mock_exec_fn):

            reply, reflection = await solver.solve(
                user_input="hello",
                registry=mock_registry,
                client=mock_client,
            )

            assert reply == "test reply"
            # max_iterations=0 时无反思
            assert reflection is None

    def test_init_default_values(self) -> None:
        """ProblemSolver 默认参数测试。"""
        solver = ProblemSolver()
        assert solver.max_iterations == 1

    def test_init_custom_iterations(self) -> None:
        """自定义迭代次数测试。"""
        solver = ProblemSolver(max_iterations=3)
        assert solver.max_iterations == 3
