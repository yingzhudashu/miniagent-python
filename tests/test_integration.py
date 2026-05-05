"""Integration test — verify all modules can be imported and basic workflows run.

This replaces tests/test.ts from the TypeScript source.
"""

import os
import tempfile
import pytest


class TestAllImports:
    """Verify all modules can be imported."""

    def test_core_modules(self):
        from src.core.agent import run_agent, get_default_agent_config
        from src.core.planner import generate_plan
        from src.core.executor import execute_plan, MODEL, get_client
        from src.core.registry import DefaultToolRegistry
        from src.core.monitor import DefaultToolMonitor
        from src.core.memory_store import memory_store
        from src.core.loop_detector import LoopDetector
        from src.core.instance_manager import InstanceManager

    def test_tools(self):
        from src.tools.filesystem import filesystem_tools
        from src.tools.exec import exec_tools
        from src.tools.web import web_tools
        from src.tools.skills import skills_tools
        from src.tools.self_opt import self_opt_tools

        assert len(filesystem_tools) > 0
        assert len(exec_tools) > 0
        assert len(web_tools) > 0

    def test_skills(self):
        from src.skills.registry import DefaultSkillRegistry
        from src.skills.loader import discover_skill_packages
        from src.skills.clawhub_client import create_clawhub_client

    def test_feishu(self):
        from src.feishu.types import FeishuConfig, FeishuMessageEvent, FeishuReply
        from src.feishu.poll_server import start_feishu_poll_server
        from src.feishu.server import create_feishu_server
        from src.feishu.agent_handler import create_feishu_handler

    def test_cli(self):
        from src.cli.cli import main as cli_main
        from src.__main__ import main as entry_main

    def test_self_opt(self):
        from src.core.self_opt.types import (
            OptimizationProposal,
            InspectionReport,
            ResearchReport,
        )
        from src.core.self_opt.inspector import inspect_project
        from src.core.self_opt.proposal_engine import generate_proposals
        from src.core.self_opt.auto_optimizer import run_auto_optimization
        from src.core.self_opt.git_snapshot import is_in_git_repo

    def test_security(self):
        from src.security.sandbox import resolve_sandbox_path, is_path_allowed

    def test_session(self):
        from src.session.manager import DefaultSessionManager


class TestBasicWorkflows:
    """Verify basic workflows work end-to-end."""

    def test_tool_registration(self):
        from src.core.registry import DefaultToolRegistry
        from src.types.tool import ToolDefinition

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
        from src.core.monitor import DefaultToolMonitor

        mon = DefaultToolMonitor()
        mon.record("read_file", 10, success=True)
        mon.record("bad_tool", 500, success=False)
        stats = mon.get_all_stats()
        assert "read_file" in stats
        assert "bad_tool" in stats
        assert mon.get_stats("read_file").calls == 1

    def test_sandbox_workflow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.security.sandbox import resolve_sandbox_path, is_path_allowed

            path = os.path.join(tmpdir, "test.txt")
            result = resolve_sandbox_path(path, [tmpdir])
            assert result.startswith(tmpdir)
            assert is_path_allowed(path, [tmpdir]) is True

    def test_loop_detector_workflow(self):
        from src.core.loop_detector import LoopDetector

        det = LoopDetector()
        # Record 8 times (critical threshold)
        for _ in range(8):
            det.record("read_file", {"path": "a.txt"}, "success")
        result = det.check("read_file", {"path": "a.txt"})
        assert result.level == "critical"

    def test_instance_manager_workflow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.core.instance_manager import InstanceManager

            mgr = InstanceManager(state_dir=tmpdir)
            assert mgr.try_acquire()["success"] is True
            mgr.release()
            assert mgr.try_acquire()["success"] is True
            mgr.release()
