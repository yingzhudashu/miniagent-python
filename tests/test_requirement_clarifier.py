"""Requirement Clarifier 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from miniagent.agent.requirement_clarifier import (
    ClarifiedRequirement,
    RequirementClarifier,
)
from miniagent.agent.types.memory import GroundTruthFact, SessionMemory
from tests.memory_helpers import make_knowledge_registry


class FakeMemoryStore:
    def __init__(self, memory: SessionMemory | None) -> None:
        self.memory = memory

    async def load(self, session_key: str) -> SessionMemory | None:
        return self.memory


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
        assert cr.resolved_assumptions == []
        assert cr.unresolved_questions == []
        assert cr.clarification_needed is False

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

    def test_to_system_prompt_with_self_clarification(self) -> None:
        """to_system_prompt 应包含自澄清依据。"""
        cr = ClarifiedRequirement(
            original="write docs",
            clarified_goal="Write docs",
            memory_resolved_facts=["输出语言是什么 -> output.language: 默认用中文"],
            default_resolved_assumptions=["输出格式是什么 -> 未指定输出格式时默认使用清晰的 Markdown"],
            unresolved_questions=["目标目录是哪一个"],
        )
        prompt = RequirementClarifier().to_system_prompt(cr)

        assert "记忆已解答" in prompt
        assert "默认假设" in prompt
        assert "仍需注意的未解问题" in prompt

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

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "test input",
                knowledge_registry=make_knowledge_registry(),
                client=mock_client,
            )

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

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "test input",
                knowledge_registry=make_knowledge_registry(),
                ask_user=mock_ask_user,
                client=mock_client,
            )

            assert len(asked_questions) == 2  # 2 个模糊点，最多 3 个
            assert "ambiguity 1" in asked_questions[0]
            assert "用户补充：user clarification answer" in result.boundary_conditions
            assert result.clarification_needed is False

    @pytest.mark.asyncio
    async def test_clarify_interactive_all_answered_clears_clarification_needed(self) -> None:
        """用户回答全部追问后不应遗留 clarification_needed。"""
        clarifier = RequirementClarifier(interactive=True)

        async def mock_ask_user(question: str) -> str:
            return "answered"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "goal",
                "ambiguity_report": ["目标目录是哪一个"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "写文件",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
            )

        assert result.clarification_needed is False
        assert result.unresolved_questions == []

    @pytest.mark.asyncio
    async def test_interactive_false_skips_ask_even_with_callback(self) -> None:
        """interactive=False 时即使有 ask_user 也不追问。"""
        clarifier = RequirementClarifier(interactive=False)
        asked = False

        async def mock_ask_user(question: str) -> str:
            nonlocal asked
            asked = True
            return "answer"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "goal",
                "ambiguity_report": ["目标目录是哪一个"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "写文件",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
            )

        assert asked is False
        assert result.clarification_needed is True

    @pytest.mark.asyncio
    async def test_on_thinking_receives_summary(self) -> None:
        """on_thinking 应收到澄清摘要。"""
        clarifier = RequirementClarifier()
        messages: list[str] = []

        async def mock_on_thinking(msg: str, *args, **kwargs) -> None:
            messages.append(msg)

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "Structured goal",
                "boundary_conditions": ["use markdown"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            await clarifier.clarify(
                "test",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                on_thinking=mock_on_thinking,
            )

        assert any("Structured goal" in m for m in messages)

    @pytest.mark.asyncio
    async def test_clarify_empty_llm_result(self) -> None:
        """LLM 返回空字典时应回落到原始输入。"""
        clarifier = RequirementClarifier()

        async def mock_llm(*args, **kwargs):
            return {}

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "original request",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
            )

        assert result.clarified_goal == "original request"
        assert result.clarification_needed is False

    @pytest.mark.asyncio
    async def test_clarify_llm_json_error_propagates(self) -> None:
        """LLM JSON 解析失败时应向上抛出异常。"""
        clarifier = RequirementClarifier()

        async def mock_llm(*args, **kwargs):
            raise ValueError("parse failed")

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            with pytest.raises(ValueError, match="parse failed"):
                await clarifier.clarify(
                    "test input",
                    knowledge_registry=make_knowledge_registry(),
                    client=MagicMock(),
                )

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

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            await clarifier.clarify(
                "clear input",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
            )
            assert asked is False  # 没有模糊点，不应追问

    @pytest.mark.asyncio
    async def test_memory_resolved_ambiguity_asks_zero_questions(self) -> None:
        """记忆可解答的模糊点不应继续追问。"""
        clarifier = RequirementClarifier(interactive=True)
        asked_questions: list[str] = []
        memory = SessionMemory(
            session_id="s",
            ground_truth_facts=[
                GroundTruthFact(
                    key="output.language",
                    value="默认用中文回答",
                    category="output_format",
                    confidence=0.95,
                )
            ],
        )

        async def mock_ask_user(question: str) -> str:
            asked_questions.append(question)
            return "answer"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "write report",
                "ambiguity_report": ["输出语言是什么"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "写报告",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
                memory_store=FakeMemoryStore(memory),
                session_key="s",
                max_questions=1,
            )

        assert asked_questions == []
        assert result.memory_resolved_facts
        assert result.clarification_needed is False

    @pytest.mark.asyncio
    async def test_safe_default_asks_zero_questions(self) -> None:
        """可用安全默认值处理的模糊点应写入默认假设而不追问。"""
        clarifier = RequirementClarifier(interactive=True)
        asked = False

        async def mock_ask_user(question: str) -> str:
            nonlocal asked
            asked = True
            return "answer"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "write summary",
                "ambiguity_report": ["输出格式是什么", "回答语言是什么"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "总结一下",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
                max_questions=3,
            )

        assert asked is False
        assert len(result.default_resolved_assumptions) == 2

    @pytest.mark.asyncio
    async def test_unresolved_questions_respect_max_questions(self) -> None:
        """不同难度传入的 max_questions 上限应限制实际追问数量。"""
        clarifier = RequirementClarifier(interactive=True)
        asked_questions: list[str] = []

        async def mock_ask_user(question: str) -> str:
            asked_questions.append(question)
            return "answer"

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "change project",
                "ambiguity_report": ["目标目录是哪一个", "要覆盖哪些文件", "迁移范围是什么"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "调整项目",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
                max_questions=2,
            )

        assert len(asked_questions) == 2
        assert len(result.unresolved_questions) == 1
        assert result.clarification_needed is True

    @pytest.mark.asyncio
    async def test_low_confidence_fact_does_not_self_clarify(self) -> None:
        """低置信事实不能静默用于自澄清。"""
        clarifier = RequirementClarifier(interactive=True)
        asked_questions: list[str] = []
        memory = SessionMemory(
            session_id="s",
            ground_truth_facts=[
                GroundTruthFact(
                    key="environment.path",
                    value="目标目录可能是 docs",
                    category="environment",
                    confidence=0.4,
                )
            ],
        )

        async def mock_ask_user(question: str) -> str:
            asked_questions.append(question)
            return ""

        async def mock_llm(*args, **kwargs):
            return {
                "clarified_goal": "write files",
                "ambiguity_report": ["目标目录是哪一个"],
            }

        with patch("miniagent.agent.requirement_clarifier.llm_json", side_effect=mock_llm):
            result = await clarifier.clarify(
                "写文件",
                knowledge_registry=make_knowledge_registry(),
                client=MagicMock(),
                ask_user=mock_ask_user,
                memory_store=FakeMemoryStore(memory),
                session_key="s",
                max_questions=1,
            )

        assert len(asked_questions) == 1
        assert result.memory_resolved_facts == []
