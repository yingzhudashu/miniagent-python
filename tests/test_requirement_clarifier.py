"""Requirement Clarifier 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from miniagent.core.requirement_clarifier import (
    ClarifiedRequirement,
    RequirementClarifier,
)


class TestRequirementClarifier:
    """RequirementClarifier 行为测试。"""

    def test_dataclass_defaults(self) -> None:
        """ClarifiedRequirement 默认值测试。"""
        cr = ClarifiedRequirement(original="hello")
        assert cr.original == "hello"
        assert cr.clarified_goal == ""
        assert cr.boundary_conditions == []
        assert cr.examples == []
        assert cr.anti_examples == []

    def test_to_system_prompt_basic(self) -> None:
        """to_system_prompt 应包含目标和约束。"""
        cr = ClarifiedRequirement(
            original="check weather",
            clarified_goal="Get weather report for Beijing",
            boundary_conditions=["must be accurate", "within 50 words"],
        )
        clarifier = RequirementClarifier()
        prompt = clarifier.to_system_prompt(cr)
        assert "Get weather report for Beijing" in prompt
        assert "must be accurate" in prompt
        assert "within 50 words" in prompt

    def test_to_system_prompt_with_examples(self) -> None:
        """to_system_prompt 应包含正反向示例。"""
        cr = ClarifiedRequirement(
            original="write code",
            clarified_goal="Write clean Python code",
            examples=["Use type hints", "Add docstrings"],
            anti_examples=["No global variables", "No bare except"],
        )
        clarifier = RequirementClarifier()
        prompt = clarifier.to_system_prompt(cr)
        assert "Use type hints" in prompt
        assert "No global variables" in prompt

    @pytest.mark.asyncio
    async def test_clarify_auto_mode(self) -> None:
        """非交互模式：仅靠 LLM 推断。"""
        clarifier = RequirementClarifier(interactive=False)
        mock_client = MagicMock()

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "Test goal",
                "boundary_conditions": ["constraint 1"],
                "output_spec": "markdown",
                "examples": [],
                "anti_examples": [],
                "ambiguity_report": ["ambiguous term"],
            }

        with patch("miniagent.core.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify("test input", client=mock_client)

            assert result.clarified_goal == "Test goal"
            assert len(result.boundary_conditions) == 1
            assert result.original == "test input"

    @pytest.mark.asyncio
    async def test_clarify_interactive_mode(self) -> None:
        """交互模式：针对模糊点追问。"""
        clarifier = RequirementClarifier(interactive=True)
        mock_client = MagicMock()

        asked_questions: list[str] = []

        async def mock_ask_user(question: str) -> str:
            asked_questions.append(question)
            return "user clarification answer"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "Interactive goal",
                "boundary_conditions": ["existing constraint"],
                "output_spec": "",
                "examples": [],
                "anti_examples": [],
                "ambiguity_report": ["ambiguity 1", "ambiguity 2"],
            }

        with patch("miniagent.core.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "test input",
                ask_user=mock_ask_user,
                client=mock_client,
            )

            assert len(asked_questions) == 2  # 2 个模糊点，最多 3 个
            assert "ambiguity 1" in asked_questions[0]
            assert "用户补充：user clarification answer" in result.boundary_conditions

    @pytest.mark.asyncio
    async def test_clarify_empty_ambiguity_no_ask(self) -> None:
        """无模糊点时不应调用 ask_user。"""
        clarifier = RequirementClarifier(interactive=True)
        asked = False

        async def mock_ask_user(question: str) -> str:
            nonlocal asked
            asked = True
            return "answer"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "clear goal",
                "ambiguity_report": [],
            }

        with patch("miniagent.core.requirement_clarifier.llm_json", side_effect=mock_llm):
            await clarifier.clarify("clear input", ask_user=mock_ask_user)
            assert asked is False  # 没有模糊点，不应追问
