"""Protocol 契约测试 — 实现类须满足 contracts.runtime 声明。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from miniagent.agent.ports.runtime import (
    ActivityLogProtocol,
    KeywordIndexProtocol,
)
from miniagent.assistant.contracts.runtime import (
    ChannelRouterProtocol,
    FeishuRuntimeProtocol,
    MessageQueueProtocol,
    UnifiedEngineProtocol,
)
from miniagent.assistant.engine.engine import UnifiedEngine
from miniagent.assistant.engine.feishu_state import FeishuRuntime
from miniagent.assistant.infrastructure.channel_router import ChannelRouter
from miniagent.assistant.infrastructure.message_queue import MessageQueueManager
from miniagent.assistant.memory.activity_log import ActivityLogger
from miniagent.assistant.memory.keyword_index import KeywordIndex


@pytest.mark.parametrize(
    "instance, protocol",
    [
        (ActivityLogger(), ActivityLogProtocol),
        (KeywordIndex(state_dir="workspaces"), KeywordIndexProtocol),
        (UnifiedEngine(), UnifiedEngineProtocol),
        (ChannelRouter(), ChannelRouterProtocol),
        (MessageQueueManager(), MessageQueueProtocol),
        (FeishuRuntime(MessageQueueManager()), FeishuRuntimeProtocol),
    ],
)
def test_implementation_satisfies_protocol(instance: object, protocol: type) -> None:
    assert isinstance(instance, protocol)


class TestActivityLogProtocolExtras:
    def test_get_stats_empty_dir(self, tmp_path: pytest.TempPathFactory) -> None:
        logger = ActivityLogger(base_dir=str(tmp_path))
        stats = logger.get_stats()
        assert stats["total_entries"] == 0
        assert stats["sessions"] == 0
        assert stats["date_range"] is None

    def test_get_stats_after_logging(self, tmp_path: pytest.TempPathFactory) -> None:
        logger = ActivityLogger(base_dir=str(tmp_path))
        logger.log_session_start("s1", "hello", "cli")
        logger.log_llm_call("s1", 1, "gpt-4o-mini", 1, 0, "think", None)
        stats = logger.get_stats()
        assert stats["total_entries"] >= 2
        assert stats["sessions"] == 1
        assert stats["date_range"] is not None
        assert stats["last_updated"] is not None

    def test_clear_old_entries(self, tmp_path: pytest.TempPathFactory) -> None:
        logger = ActivityLogger(base_dir=str(tmp_path))
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        old_path = tmp_path / f"{old_date}.md"
        old_path.write_text("## old\n", encoding="utf-8")
        logger.log_session_start("s1", "recent", "cli")
        removed = logger.clear_old_entries(days=30)
        assert removed == 1
        assert not old_path.exists()
        assert logger.get_stats()["sessions"] == 1


class TestUnifiedEngineThinkingDisplay:
    def test_get_thinking_display_returns_thinking(self) -> None:
        engine = UnifiedEngine()
        assert engine.get_thinking_display() is engine.thinking


class TestChannelRouterGetPrimary:
    def test_get_primary_matches_property(self) -> None:
        router = ChannelRouter()
        router.set_primary("sess-a")
        assert router.get_primary() == router.primary == "sess-a"


class TestMessageQueueProtocolSurface:
    @pytest.mark.asyncio
    async def test_dispatch_runs_coro(self) -> None:
        mq = MessageQueueManager()
        seen: list[str] = []

        async def job() -> None:
            seen.append("done")

        await mq.dispatch_wait("__cli__", job())
        assert seen == ["done"]

        status = mq.get_agent_status("__cli__")
        assert status["status"] in ("idle", "processing")

        all_status = mq.get_status()
        assert "mode" in all_status
        assert "chats" in all_status

        abort = mq.abort_chat("__cli__")
        assert "chat_id" in abort
