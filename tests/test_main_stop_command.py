"""模块入口 ``--stop`` 的实例选择、退出码与部分失败测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent import __main__ as main_module


def _patch_instances(monkeypatch, *, stop_success: bool = True) -> list[tuple[int, str]]:
    import miniagent.infrastructure.instance as instance_module

    instances = [
        {"instance_id": 1, "state_dir": "state-a", "pid": 10},
        {"instance_id": 2, "state_dir": "state-b", "pid": 20},
    ]
    stopped: list[tuple[int, str]] = []
    monkeypatch.setattr(instance_module, "list_instances", lambda: instances)
    monkeypatch.setattr(instance_module, "format_instances_table", lambda _items: "TABLE")

    def stop(instance_id, *, state_dir):
        stopped.append((instance_id, state_dir))
        return {"success": stop_success, "reason": "failed"}

    monkeypatch.setattr(instance_module, "stop_instance_by_id", stop)
    return stopped


def test_stop_all_success_and_partial_failure(monkeypatch, capsys) -> None:
    stopped = _patch_instances(monkeypatch)
    monkeypatch.setattr(main_module.sys, "argv", ["miniagent", "--stop", "--all"])
    assert main_module._run_stop_command() == 0
    assert stopped == [(1, "state-a"), (2, "state-b")]
    assert "已停止" in capsys.readouterr().out

    stopped = _patch_instances(monkeypatch, stop_success=False)
    assert main_module._run_stop_command() == 1
    assert len(stopped) == 2


def test_stop_non_tty_and_bad_arguments(monkeypatch, capsys) -> None:
    _patch_instances(monkeypatch)
    monkeypatch.setattr(main_module.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(main_module.sys, "argv", ["miniagent", "--stop"])
    assert main_module._run_stop_command() == 1
    monkeypatch.setattr(main_module.sys, "argv", ["miniagent", "--stop", "unknown"])
    assert main_module._run_stop_command() == 2
    monkeypatch.setattr(main_module.sys, "argv", ["miniagent", "--stop", "--state-dir"])
    assert main_module._run_stop_command() == 2
    output = capsys.readouterr().out
    assert "不是交互式" in output and "用法" in output


@pytest.mark.parametrize("answer", ["", "q", "quit", "exit"])
def test_stop_interactive_cancel(monkeypatch, answer) -> None:
    _patch_instances(monkeypatch)
    monkeypatch.setattr(main_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(main_module.sys, "argv", ["miniagent", "--stop"])
    monkeypatch.setattr("builtins.input", lambda: answer)
    assert main_module._run_stop_command() == 0


def test_stop_no_instances(monkeypatch) -> None:
    import miniagent.infrastructure.instance as instance_module

    monkeypatch.setattr(instance_module, "list_instances", lambda: [])
    monkeypatch.setattr(instance_module, "format_instances_table", lambda _items: "none")
    assert main_module._run_stop_command() == 0
