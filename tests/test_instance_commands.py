"""Tests for /instance CLI command handler."""

from unittest.mock import patch

from miniagent.engine.commands.instance_commands import (
    cmd_instance_handler,
    format_instance_command_usage,
)


class TestFormatInstanceCommandUsage:
    def test_includes_subcommands(self):
        usage = format_instance_command_usage()
        assert "/instance list" in usage
        assert "/instance stop" in usage
        assert "--state-dir" in usage


class TestCmdInstanceHandler:
    def test_list_empty(self, capsys):
        with patch(
            "miniagent.infrastructure.instance.list_instances",
            return_value=[],
        ), patch(
            "miniagent.infrastructure.instance.format_instances_table",
            return_value="📭 暂无运行实例",
        ):
            cmd_instance_handler(["/instance", "list"], "list", {"instance_id": 1})
        assert "暂无运行实例" in capsys.readouterr().out

    def test_stop_without_id(self, capsys):
        cmd_instance_handler(["/instance", "stop"], "stop", {"instance_id": 1})
        out = capsys.readouterr().out
        assert "缺少实例 ID" in out
        assert "/instance stop" in out

    def test_stop_unknown_subcommand(self, capsys):
        cmd_instance_handler(["/instance", "foo"], "foo", {"instance_id": 1})
        out = capsys.readouterr().out
        assert "未知的子命令: foo" in out
        assert "/instance list" in out

    def test_stop_failure_shows_reason(self, capsys):
        fake_inst = {
            "instance_id": 2,
            "state_dir": "/tmp/registry",
            "pid": 99999,
        }
        with patch(
            "miniagent.infrastructure.instance.list_instances",
            return_value=[fake_inst],
        ), patch(
            "miniagent.infrastructure.instance.stop_instance_by_id",
            return_value={"success": False, "reason": "无法终止 PID=99999: access denied"},
        ):
            cmd_instance_handler(["/instance", "stop", "2"], "stop", {"instance_id": 1})
        out = capsys.readouterr().out
        assert "无法终止 PID=99999" in out
        assert "停止失败" not in out

    def test_stop_success_without_reason(self, capsys):
        fake_inst = {"instance_id": 2, "state_dir": "/tmp/registry", "pid": 99999}
        with patch(
            "miniagent.infrastructure.instance.list_instances",
            return_value=[fake_inst],
        ), patch(
            "miniagent.infrastructure.instance.stop_instance_by_id",
            return_value={"success": True},
        ):
            cmd_instance_handler(["/instance", "stop", "2"], "stop", {"instance_id": 1})
        out = capsys.readouterr().out.strip()
        assert out.endswith("实例 #2 已停止")
        assert "已停止:" not in out

    def test_stop_success_with_reason(self, capsys):
        fake_inst = {"instance_id": 2, "state_dir": "/tmp/registry", "pid": 99999}
        with patch(
            "miniagent.infrastructure.instance.list_instances",
            return_value=[fake_inst],
        ), patch(
            "miniagent.infrastructure.instance.stop_instance_by_id",
            return_value={
                "success": True,
                "reason": "实例 #2 (PID=99999) 已不存在，已清理",
            },
        ):
            cmd_instance_handler(["/instance", "stop", "2"], "stop", {"instance_id": 1})
        out = capsys.readouterr().out
        assert "已不存在，已清理" in out

    def test_stop_current_instance_blocked(self, capsys):
        cmd_instance_handler(["/instance", "stop", "1"], "stop", {"instance_id": 1})
        out = capsys.readouterr().out
        assert "不能停止当前实例" in out

    def test_stop_invalid_id(self, capsys):
        cmd_instance_handler(["/instance", "stop", "abc"], "stop", {"instance_id": 1})
        out = capsys.readouterr().out
        assert "无效的实例 ID" in out
