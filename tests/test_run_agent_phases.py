"""四阶段求解集成测试 — 验证 run_agent() 中澄清→规划→执行→反思链路。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.task_classifier import TaskDifficulty
from miniagent.agent.types.planning import StructuredPlan
from miniagent.agent.types.tool import Toolbox
from tests.config_helpers import install_test_config
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime


def _make_agent_config():
    """构造最小可用的 AgentConfig mock。"""
    cfg = MagicMock()
    cfg.log_file = None
    cfg.session_config.session_registry = None
    cfg.session_config.session_workspace = None
    cfg.session_config.session_key = None
    cfg.session_config.conversation_history = []
    cfg.feishu_config.cli_loop_state = None
    cfg.feishu_config.cli_dispatch_allow_mutations = True
    cfg.feishu_config.receive_chat_id = None
    cfg.feishu_config.im_receive_id_type = None
    cfg.feishu_config.im_receive_id = None
    cfg.loop_detection = None
    cfg.context_compress_threshold = 0.8
    cfg.context_overflow_strategy = "truncate"
    cfg.debug = False
    cfg.max_turns = 1
    cfg.tool_timeout = 30
    cfg.tool_selection_strategy = "all"
    cfg.allow_parallel_tools = False
    cfg.risk_level = None
    return cfg


_TC_PATH = "miniagent.agent.task_classifier.task_classifier_enabled"
_REFLECT_PATH = "miniagent.agent.agent.reflect_on_result"


class TestRunAgentClarification:
    """测试 Phase 0 需求澄清。"""

    @pytest.mark.asyncio
    async def test_clarifier_enhances_input(self, tmp_path):
        """传入 clarifier 时，clarify 被调用且澄清结果注入。"""
        from miniagent.agent.requirement_clarifier import ClarifiedRequirement

        install_test_config(
            tmp_path,
            {"features": {"requirement_clarify": True, "reflection": False}},
        )

        clarified = ClarifiedRequirement(
            original="查天气",
            clarified_goal="获取指定城市的天气预报",
            boundary_conditions=["需要城市名称"],
        )

        clarifier = MagicMock()
        clarifier.clarify = AsyncMock(return_value=clarified)
        clarifier.to_system_prompt = MagicMock(return_value="## 需求规格\n目标：获取指定城市的天气预报")

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "天气预报结果"

                        registry = MagicMock()
                        registry.get_schemas.return_value = []
                        registry.get_all.return_value = {}
                        registry.list.return_value = []

                        from miniagent.agent.agent import run_agent

                        reply = await run_agent(
                            "查天气",
                            registry=registry,
                            memory=make_memory_runtime(),
                            knowledge_registry=make_knowledge_registry(),
                            client=MagicMock(),
                            clarifier=clarifier,
                        )

                        assert reply.reply == "天气预报结果"
                        clarifier.clarify.assert_called_once()
                        mock_exec.assert_called_once()
                        call_args = mock_exec.call_args
                        assert "## 需求规格" in call_args[0][1]
                        assert "目标：" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_env_disable_clarify(self, tmp_path):
        """features.requirement_clarify=false 时不执行澄清。"""
        install_test_config(
            tmp_path,
            {"features": {"requirement_clarify": False, "reflection": False}},
        )

        clarifier = MagicMock()
        clarifier.clarify = AsyncMock()

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "结果"

                        registry = MagicMock()
                        registry.get_schemas.return_value = []
                        registry.get_all.return_value = {}
                        registry.list.return_value = []

                        from miniagent.agent.agent import run_agent

                        reply = await run_agent(
                            "查天气",
                            registry=registry,
                            memory=make_memory_runtime(),
                            knowledge_registry=make_knowledge_registry(),
                            client=MagicMock(),
                            clarifier=clarifier,
                        )

                        clarifier.clarify.assert_not_called()
                        assert reply.reply == "结果"

    @pytest.mark.asyncio
    async def test_no_clarifier_no_error(self, tmp_path):
        """不传入 clarifier 时正常运行。"""
        install_test_config(tmp_path, {"features": {"reflection": False}})

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "结果"

                        registry = MagicMock()
                        registry.get_schemas.return_value = []
                        registry.get_all.return_value = {}
                        registry.list.return_value = []

                        from miniagent.agent.agent import run_agent

                        reply = await run_agent(
                            "test",
                            registry=registry,
                            memory=make_memory_runtime(),
                            knowledge_registry=make_knowledge_registry(),
                            client=MagicMock(),
                        )

                        assert reply.reply == "结果"


class TestClarificationMaxQuestionsByDifficulty:
    """测试不同难度级别的澄清追问数量限制。"""

    @pytest.mark.asyncio
    async def test_normal_difficulty_max_1_question(self, tmp_path):
        """NORMAL（一般）难度最多问 1 个问题。"""
        install_test_config(
            tmp_path,
            {"features": {"requirement_clarify": True, "reflection": False}},
        )
        tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

        clarifier = MagicMock()
        clarifier.clarify = AsyncMock()
        clarifier.clarify.return_value = MagicMock(clarified_goal="")

        with (
            patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
            patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        ):
            with patch("miniagent.agent.agent.classify_task_difficulty", new_callable=AsyncMock) as clf:
                clf.return_value = TaskDifficulty.NORMAL
                with patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp:
                    gp.return_value = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex:
                        ex.return_value = "ok"

                        from miniagent.agent.agent import run_agent
                        from miniagent.agent.tools.registry import DefaultToolRegistry

                        await run_agent(
                            "task",
                            registry=DefaultToolRegistry(),
                            memory=make_memory_runtime(),
                            knowledge_registry=make_knowledge_registry(),
                            client=MagicMock(),
                            toolboxes=[tb],
                            clarifier=clarifier,
                        )

        clarifier.clarify.assert_called_once()
        call_kwargs = clarifier.clarify.call_args.kwargs
        assert call_kwargs.get("max_questions") == 1

    @pytest.mark.asyncio
    async def test_medium_difficulty_max_2_questions(self, tmp_path):
        """MEDIUM（中等）难度最多问 2 个问题。"""
        install_test_config(
            tmp_path,
            {"features": {"requirement_clarify": True, "reflection": False}},
        )
        tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

        clarifier = MagicMock()
        clarifier.clarify = AsyncMock()
        clarifier.clarify.return_value = MagicMock(clarified_goal="")

        with (
            patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
            patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        ):
            with patch("miniagent.agent.agent.classify_task_difficulty", new_callable=AsyncMock) as clf:
                clf.return_value = TaskDifficulty.MEDIUM
                with patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp:
                    gp.return_value = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex:
                        ex.return_value = "ok"

                        from miniagent.agent.agent import run_agent
                        from miniagent.agent.tools.registry import DefaultToolRegistry

                        await run_agent(
                            "task",
                            registry=DefaultToolRegistry(),
                            memory=make_memory_runtime(),
                            knowledge_registry=make_knowledge_registry(),
                            client=MagicMock(),
                            toolboxes=[tb],
                            clarifier=clarifier,
                        )

        clarifier.clarify.assert_called_once()
        call_kwargs = clarifier.clarify.call_args.kwargs
        assert call_kwargs.get("max_questions") == 2

    @pytest.mark.asyncio
    async def test_complex_difficulty_max_3_questions(self, tmp_path):
        """COMPLEX（复杂）难度最多问 3 个问题。"""
        install_test_config(
            tmp_path,
            {"features": {"requirement_clarify": True, "reflection": False}},
        )
        tb = Toolbox(id="fs", name="fs", description="files", keywords=[])

        clarifier = MagicMock()
        clarifier.clarify = AsyncMock()
        clarifier.clarify.return_value = MagicMock(clarified_goal="")

        with (
            patch("miniagent.agent.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", True),
            patch("miniagent.agent.constants.EXECUTION_ANNOUNCE_DIFFICULTY", False),
        ):
            with patch("miniagent.agent.agent.classify_task_difficulty", new_callable=AsyncMock) as clf:
                clf.return_value = TaskDifficulty.COMPLEX
                with patch("miniagent.agent.agent.generate_plan", new_callable=AsyncMock) as gp:
                    gp.return_value = StructuredPlan(summary="s", steps=[], required_toolboxes=[])
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as ex:
                        ex.return_value = "ok"

                        from miniagent.agent.agent import run_agent
                        from miniagent.agent.tools.registry import DefaultToolRegistry

                        await run_agent(
                            "task",
                            registry=DefaultToolRegistry(),
                            memory=make_memory_runtime(),
                            knowledge_registry=make_knowledge_registry(),
                            client=MagicMock(),
                            toolboxes=[tb],
                            clarifier=clarifier,
                        )

        clarifier.clarify.assert_called_once()
        call_kwargs = clarifier.clarify.call_args.kwargs
        assert call_kwargs.get("max_questions") == 3


class TestRunAgentReflection:
    """测试 Phase 3 反思评估。"""

    @pytest.mark.asyncio
    async def test_reflection_when_enabled(self, tmp_path):
        """默认开启反思评估。"""
        from miniagent.agent.problem_solver import ReflectionResult

        install_test_config(tmp_path, {"features": {"reflection": True}})

        cfg = _make_agent_config()
        cfg.session_config.session_key = "test_session"

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=cfg):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "Agent 回复结果"

                        on_thinking = AsyncMock()

                        with patch(_REFLECT_PATH, new_callable=AsyncMock) as mock_reflect:
                            mock_reflect.return_value = ReflectionResult(
                                acceptable=True,
                                quality_score=0.8,
                                issues=[],
                                suggestions=[],
                            )

                            registry = MagicMock()
                            registry.get_schemas.return_value = []
                            registry.get_all.return_value = {}
                            registry.list.return_value = []

                            from miniagent.agent.agent import run_agent

                            reply = await run_agent(
                                "test input",
                                registry=registry,
                                memory=make_memory_runtime(),
                                knowledge_registry=make_knowledge_registry(),
                                client=MagicMock(),
                                on_thinking=on_thinking,
                            )

                            mock_reflect.assert_called_once()
                            assert "Agent 回复结果" in reply.reply
                            assert "质量评估" in reply.reply

    @pytest.mark.asyncio
    async def test_reflection_explicitly_disabled(self, tmp_path):
        """features.reflection=false 时不执行反思。"""
        install_test_config(tmp_path, {"features": {"reflection": False}})

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "结果"

                        registry = MagicMock()
                        registry.get_schemas.return_value = []
                        registry.get_all.return_value = {}
                        registry.list.return_value = []

                        with patch(_REFLECT_PATH, new_callable=AsyncMock) as mock_reflect:
                            from miniagent.agent.agent import run_agent

                            reply = await run_agent(
                                "test",
                                registry=registry,
                                memory=make_memory_runtime(),
                                knowledge_registry=make_knowledge_registry(),
                                client=MagicMock(),
                            )

                            mock_reflect.assert_not_called()
                            assert reply.reply == "结果"
