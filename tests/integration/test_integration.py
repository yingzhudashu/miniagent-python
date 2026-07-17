"""Integration smoke — sandbox-only; tools/registry covered by dedicated tests."""

import os
import tempfile


class TestBasicWorkflows:
    """Lightweight cross-module smoke (detailed tests live elsewhere)."""

    def test_sandbox_workflow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from miniagent.assistant.security.sandbox import is_path_allowed, resolve_sandbox_path

            path = os.path.join(tmpdir, "test.txt")
            result = resolve_sandbox_path(path, [tmpdir])
            assert result.startswith(tmpdir)
            assert is_path_allowed(path, [tmpdir]) is True
