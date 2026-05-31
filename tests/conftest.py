"""Test configuration for miniagent-python."""

import os
import sys
import tempfile

import pytest

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture()
def state_dir(monkeypatch: pytest.MonkeyPatch) -> str:
    """Isolated MINI_AGENT_STATE directory for scheduled_tasks / store tests."""
    d = tempfile.mkdtemp()
    monkeypatch.setenv("MINI_AGENT_STATE", d)
    return d


@pytest.fixture(autouse=True)
def _reset_process_singletons_after_test() -> None:
    """Teardown：重置进程级默认记忆 bundle 与共享 AsyncOpenAI，减轻测试顺序敏感。"""
    from miniagent.core.executor import _reset_env_caches_for_tests
    from miniagent.core.openai_client import reset_shared_async_openai_for_tests
    from miniagent.infrastructure.loop_detector import clear_args_cache
    from miniagent.memory.defaults import reset_process_default_memory_bundle_for_tests

    yield
    reset_process_default_memory_bundle_for_tests()
    reset_shared_async_openai_for_tests()
    _reset_env_caches_for_tests()
    clear_args_cache()
