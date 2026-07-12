"""Tests for session manager."""

import os
import tempfile

import pytest

from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.session.manager import DefaultSessionManager
from miniagent.types.config import normalize_conversation_history


def test_normalize_conversation_history_rejects_wrapped_dict():
    raw = {
        "session_id": "default",
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = normalize_conversation_history(raw)
    assert out == []


def test_normalize_conversation_history_filters_non_dict():
    raw = [{"role": "user", "content": "a"}, "skip", {"role": "assistant", "content": "b"}]
    out = normalize_conversation_history(raw)
    assert len(out) == 2


class TestDefaultSessionManager:
    @pytest.fixture(autouse=True)
    def setup(self):
        # Set a temp state dir for the test
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["MINIAGENT_PATHS_STATE_DIR"] = self.tmpdir.name
        self.main_registry = DefaultToolRegistry()
        self.manager = DefaultSessionManager(self.main_registry)
        yield
        self.tmpdir.cleanup()
        # Clean up env
        if "MINIAGENT_PATHS_STATE_DIR" in os.environ:
            del os.environ["MINIAGENT_PATHS_STATE_DIR"]

    def test_create_session(self):
        session = self.manager.get_or_create("test-1")
        assert session is not None
        assert session.id == "test-1"

    def test_list_sessions(self):
        self.manager.get_or_create("s1")
        self.manager.get_or_create("s2")
        sessions = self.manager.list()
        assert len(sessions) == 2

    def test_destroy_session(self):
        self.manager.get_or_create("to-destroy")
        result = self.manager.destroy("to-destroy")
        assert result is True
        assert self.manager.get("to-destroy") is None

    def test_destroy_nonexistent(self):
        result = self.manager.destroy("ghost")
        assert result is False

    def test_session_has_workspace(self):
        session = self.manager.get_or_create("ws-test")
        assert session.workspace_path is not None
        assert os.path.exists(session.workspace_path)
