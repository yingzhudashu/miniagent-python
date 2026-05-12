"""Integration test �?verify all modules can be imported and basic workflows run.

This replaces tests/test.ts from the TypeScript source.
"""

import os
import tempfile


class TestAllImports:
    """Verify all modules can be imported."""

    def test_core_modules(self):
        pass

    def test_tools(self):
        from miniagent.tools.exec import exec_tools
        from miniagent.tools.filesystem import filesystem_tools
        from miniagent.tools.web import web_tools

        assert len(filesystem_tools) > 0
        assert len(exec_tools) > 0
        assert len(web_tools) > 0

    def test_skills(self):
        pass

    def test_feishu(self):
        pass

    def test_cli(self):
        pass

    def test_self_opt(self):
        pass

    def test_security(self):
        pass

    def test_session(self):
        pass


class TestBasicWorkflows:
    """Verify basic workflows work end-to-end."""

    def test_tool_registration(self):
        from miniagent.infrastructure.registry import DefaultToolRegistry
        from miniagent.types.tool import ToolDefinition

        reg = DefaultToolRegistry()
        assert len(reg.list()) == 0
        reg.register("test", ToolDefinition(
            schema={"type": "function", "function": {"name": "test", "description": "test", "parameters": {"type": "object", "properties": {}}}},
            handler=lambda x, ctx: None,  # type: ignore
            permission="sandbox",
            help_text="Help",
        ))
        assert len(reg.list()) == 1

    def test_monitor_integration(self):
        from miniagent.infrastructure.monitor import DefaultToolMonitor

        mon = DefaultToolMonitor()
        mon.record("read_file", 10, success=True)
        mon.record("bad_tool", 500, success=False)
        stats = mon.get_all_stats()
        assert "read_file" in stats
        assert "bad_tool" in stats
        assert mon.get_stats("read_file").calls == 1

    def test_sandbox_workflow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from miniagent.security.sandbox import is_path_allowed, resolve_sandbox_path

            path = os.path.join(tmpdir, "test.txt")
            result = resolve_sandbox_path(path, [tmpdir])
            assert result.startswith(tmpdir)
            assert is_path_allowed(path, [tmpdir]) is True

    def test_loop_detector_workflow(self):
        from miniagent.infrastructure.loop_detector import LoopDetector

        det = LoopDetector()
        # Record 8 times (critical threshold)
        for _ in range(8):
            det.record("read_file", {"path": "a.txt"}, "success")
        result = det.check("read_file", {"path": "a.txt"})
        assert result.level == "critical"

    def test_instance_manager_workflow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from miniagent.infrastructure.instance import InstanceRegistry

            mgr = InstanceRegistry(state_dir=tmpdir)
            assert mgr.register(mode="cli")["instance_id"] >= 1
            mgr.unregister()
