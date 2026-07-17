"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_command_dispatch_instance_and_query() -> None:
    from miniagent.assistant.engine.command_dispatch import dispatch_command
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager

    queue = MessageQueueManager()
    runtime = SimpleNamespace(
        message_queue=queue,
        channel_router=SimpleNamespace(),
        feishu=SimpleNamespace(),
    )
    state = {"runtime_ctx": runtime, "instance_id": -1}
    instance_output = await dispatch_command("/instance list", state=state, capture=True)
    query_output = await dispatch_command("/query", state=state, capture=True)
    assert isinstance(instance_output, str)
    assert isinstance(query_output, str)
