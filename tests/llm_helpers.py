"""Shared LLM mock helpers for tests that need to mock OpenAI client.

Provides a context manager that mocks get_shared_async_openai across all modules
that import it, avoiding RuntimeError when OPENAI_API_KEY is not set.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_llm_client() -> MagicMock:
    """创建 mock LLM client，避免需要真实 API key。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()
    return mock_client


@contextlib.contextmanager
def mock_all_llm_clients():
    """Mock 所有模块中的 get_shared_async_openai 引用。

    包括：
    - miniagent.core.openai_client（源函数）
    - miniagent.core.task_classifier
    - miniagent.core.planner
    - miniagent.core.executor
    """
    mock_client = _make_mock_llm_client()
    patches = [
        patch("miniagent.core.openai_client.get_shared_async_openai", return_value=mock_client),
        patch("miniagent.core.task_classifier.get_shared_async_openai", return_value=mock_client),
        patch("miniagent.core.planner.get_shared_async_openai", return_value=mock_client),
        patch("miniagent.core.executor.get_shared_async_openai", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        yield mock_client
    finally:
        for p in patches:
            p.stop()


__all__ = ["mock_all_llm_clients", "_make_mock_llm_client"]