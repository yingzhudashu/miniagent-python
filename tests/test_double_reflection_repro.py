"""复现/证伪「每个答案末尾出现两次质量评估」bug 的运行时测试。

逐一验证四个假设：
- A：单条消息触发了两次 run_agent / reflect_on_result
- B：reply 在 CLI 被渲染两次
- C：thinking 回调被注册两次导致输出翻倍
- D：footer 累积进会话历史，下一轮被 LLM 复述 + reflect 再追加 = 两次

运行：python -m pytest tests/test_double_reflection_repro.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.problem_solver import ReflectionResult
from tests.config_helpers import install_test_config
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime

_TC_PATH = "miniagent.agent.task_classifier.task_classifier_enabled"
_REFLECT_PATH = "miniagent.agent.agent.reflect_on_result"

_FOOTER_MARK = "质量评估"
_SCORE_MARK = "质量评分"


def _make_agent_config(history=None):
    """构造最小可用的 AgentConfig mock（可注入 conversation_history）。"""
    cfg = MagicMock()
    cfg.log_file = None
    cfg.session_config.session_registry = None
    cfg.session_config.session_workspace = None
    cfg.session_config.session_key = "repro_session"
    cfg.session_config.conversation_history = history if history is not None else []
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


def _make_registry():
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.get_all.return_value = {}
    registry.list.return_value = []
    return registry


def _count_footers(text: str) -> int:
    """统计 reply 中「质量评分」标记出现的次数（每个 footer 含且仅含一次）。"""
    return text.count(_SCORE_MARK)


# ──────────────────────────────────────────────────────────────────────
# 假设 A：单条消息是否触发两次 run_agent / reflect_on_result
# ──────────────────────────────────────────────────────────────────────
class TestHypothesisA_DoubleCall:
    @pytest.mark.asyncio
    async def test_single_run_agent_calls_reflect_once(self, tmp_path):
        """一次 run_agent 调用，reflect_on_result 恰好被调用 1 次，footer 恰好 1 个。"""
        install_test_config(tmp_path, {"features": {"reflection": True}})

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "这是答案正文。"
                        with patch(_REFLECT_PATH, new_callable=AsyncMock) as mock_reflect:
                            mock_reflect.return_value = ReflectionResult(
                                acceptable=True, quality_score=0.8
                            )
                            from miniagent.agent import run_agent

                            reply = await run_agent(
                                "问题",
                                registry=_make_registry(),
                                memory=make_memory_runtime(),
                                knowledge_registry=make_knowledge_registry(),
                                client=MagicMock(),
                            )

        assert mock_reflect.call_count == 1, "单次 run_agent 内 reflect 被调用次数应为 1"
        assert _count_footers(reply.reply) == 1, (
            f"单次 run_agent 的 reply 应只含 1 个 footer，实际 {_count_footers(reply.reply)} 个"
        )

    @pytest.mark.asyncio
    async def test_reflect_receives_reply_without_existing_footer(self, tmp_path):
        """reflect 收到的 reply（execute_plan 返回值）本身不应已含 footer（排除 execute_plan 自带 footer）。"""
        install_test_config(tmp_path, {"features": {"reflection": True}})

        captured = {}

        async def _capture_reflect(user_input, reply, **kwargs):
            captured["reply"] = reply
            return ReflectionResult(acceptable=True, quality_score=0.8)

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "这是答案正文。"
                        with patch(_REFLECT_PATH, side_effect=_capture_reflect):
                            from miniagent.agent import run_agent

                            await run_agent(
                                "问题",
                                registry=_make_registry(),
                                memory=make_memory_runtime(),
                                knowledge_registry=make_knowledge_registry(),
                                client=MagicMock(),
                            )

        assert _SCORE_MARK not in captured["reply"], (
            "execute_plan 返回的 reply 不应自带 footer；若含说明 footer 来自 LLM 复述历史"
        )


# ──────────────────────────────────────────────────────────────────────
# 假设 B：reflect_on_result 是否经 on_thinking 把 footer 再走一遍渲染
# ──────────────────────────────────────────────────────────────────────
class TestHypothesisB_ReflectThinkingSink:
    @pytest.mark.asyncio
    async def test_reflect_called_with_on_thinking_none(self, tmp_path):
        """agent.py:567 应以 on_thinking=None 调 reflect，避免评估文本经 thinking sink 再渲染一遍。"""
        install_test_config(tmp_path, {"features": {"reflection": True}})

        captured = {}

        async def _capture_reflect(
            user_input,
            reply,
            *,
            knowledge_registry,
            client=None,
            on_thinking="MISSING",
            session_key=None,
        ):
            captured["on_thinking"] = on_thinking
            return ReflectionResult(acceptable=True, quality_score=0.8)

        on_thinking_cb = AsyncMock()

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=_make_agent_config()):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = "答案正文。"
                        with patch(_REFLECT_PATH, side_effect=_capture_reflect):
                            from miniagent.agent import run_agent

                            await run_agent(
                                "问题",
                                registry=_make_registry(),
                                memory=make_memory_runtime(),
                                knowledge_registry=make_knowledge_registry(),
                                client=MagicMock(),
                                on_thinking=on_thinking_cb,
                            )

        assert captured["on_thinking"] is None, (
            "reflect 应以 on_thinking=None 调用，否则评估文本会经 thinking sink 额外渲染"
        )


# ──────────────────────────────────────────────────────────────────────
# 假设 D：footer 进入会话历史，回灌 LLM 时未被剥离 → 下一轮 LLM 复述 + reflect 追加
# ──────────────────────────────────────────────────────────────────────
class TestHypothesisD_FooterAccumulatesInHistory:
    def test_footer_stripped_before_llm_context(self):
        """conversation_history_for_llm 会剥离反思 footer，避免回灌 LLM 后复述。"""
        from miniagent.agent.history import conversation_history_for_llm

        prior_reply = "上一轮答案。\n\n---\n🤖 质量评估通过 | 质量评分 0.8"
        history = [
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": prior_reply},
        ]
        out = conversation_history_for_llm(history)
        asst = [m for m in out if m["role"] == "assistant"]
        assert len(asst) == 1
        assert _SCORE_MARK not in asst[0]["content"], (
            "footer 应在回灌 LLM 前被 strip_reflection_footer 剥离"
        )
        assert asst[0]["content"] == "上一轮答案。"

    @pytest.mark.asyncio
    async def test_llm_echoes_prior_footer_then_reflect_appends_second(self, tmp_path):
        """模拟下一轮：execute_plan 的 LLM 复述了历史里的 footer，reflect 再追加 → 两个 footer。

        这是对「双重质量评估」根因机制的运行时复现：
        execute_plan 返回值已含一个 footer（LLM 从历史复述），Phase 3 再 append 一个。
        """
        install_test_config(tmp_path, {"features": {"reflection": True}})

        # 上一轮 footer 已落历史
        prior_footer = "\n\n---\n🤖 质量评估通过 | 质量评分 0.8"
        history = [
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": "上一轮答案。" + prior_footer},
        ]
        cfg = _make_agent_config(history=history)

        # execute_plan 的 LLM 看到历史 footer 后，在本轮正文里复述了同样的 footer
        echoed_reply = "本轮答案正文。" + prior_footer

        with patch("miniagent.agent.agent.get_default_agent_config", return_value=cfg):
            with patch("miniagent.agent.agent.merge_agent_config", side_effect=lambda a, b: a):
                with patch(_TC_PATH, return_value=False):
                    with patch("miniagent.agent.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = echoed_reply
                        with patch(_REFLECT_PATH, new_callable=AsyncMock) as mock_reflect:
                            mock_reflect.return_value = ReflectionResult(
                                acceptable=True, quality_score=0.9
                            )
                            from miniagent.agent import run_agent

                            reply = await run_agent(
                                "本轮问题",
                                registry=_make_registry(),
                                memory=make_memory_runtime(),
                                knowledge_registry=make_knowledge_registry(),
                                client=MagicMock(),
                            )

        n = _count_footers(reply.reply)
        assert n == 2, (
            f"复现双重质量评估：当 LLM 从历史复述 footer 后，最终 reply 含 {n} 个 footer"
        )
