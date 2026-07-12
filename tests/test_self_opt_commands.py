"""自优化命令处理器的离线展示、校验与状态转换测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from miniagent.engine.commands import self_opt_commands as commands


class _Store:
    records: dict[str, dict] = {}
    update_result = True
    apply_result = SimpleNamespace(status="success", changes_applied=2, error=None)

    def load_proposals(self, status=None):
        values = list(self.records.values())
        return [record for record in values if status is None or record.get("status") == status]

    def get_proposal(self, proposal_id):
        return self.records.get(proposal_id)

    def update_status(self, proposal_id, target):
        if not self.update_result or proposal_id not in self.records:
            return False
        self.records[proposal_id]["status"] = target
        return True

    async def apply_proposal_async(self, _proposal_id, *, root, manual):
        assert manual is True
        assert isinstance(root, str)
        return self.apply_result


@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch, tmp_path: Path):
    import miniagent.core.self_opt.proposal_generator as generator_module
    import miniagent.core.self_opt.proposal_store as store_module
    import miniagent.infrastructure.json_config as config_module

    _Store.records = {}
    _Store.update_result = True
    _Store.apply_result = SimpleNamespace(status="success", changes_applied=2, error=None)
    monkeypatch.setattr(store_module, "ProposalStore", _Store)
    monkeypatch.setattr(store_module, "get_proposal_output_dir", lambda: tmp_path / "proposals")
    monkeypatch.setattr(store_module, "get_reports_dir", lambda: tmp_path / "reports")
    monkeypatch.setattr(generator_module, "ProposalGenerator", lambda: SimpleNamespace(generate_and_save=lambda: []))
    values = {
        "self_optimization.enabled": True,
        "self_optimization.auto_apply": False,
        "self_optimization.auto_apply_max_risk": "low",
        "self_optimization.runtime_analysis_enabled": True,
        "self_optimization.code_analysis_enabled": True,
    }
    monkeypatch.setattr(config_module, "get_config", lambda key, default=None: values.get(key, default))
    return SimpleNamespace(tmp_path=tmp_path, values=values, generator_module=generator_module)


def _record(status="pending", *, risk="low") -> dict:
    return {
        "id": "p1",
        "status": status,
        "source": "runtime",
        "created_at": "2026-01-01",
        "updated_at": "2026-01-02",
        "proposal": {
            "type": "performance",
            "risk_level": risk,
            "target": "module",
            "description": "描述" * 30,
            "rationale": "理由",
            "expected_benefit": "收益",
            "estimated_effort": 15,
            "files": [{"action": "modify", "path": "a.py", "reason": "测试"}],
            "test_cases": [{"id": "t1", "description": "回归"}],
        },
    }


@pytest.mark.asyncio
async def test_handle_self_opt_dispatches_and_validates(monkeypatch) -> None:
    import miniagent.infrastructure.json_config as config_module

    assert "系统状态" in await commands.handle_self_opt("/self-opt status", capture=True)
    assert "用法" in await commands.handle_self_opt("/self-opt show", capture=True)
    assert "用法" in await commands.handle_self_opt("/self-opt apply", capture=True)
    assert "未知" in await commands.handle_self_opt("/self-opt nope", capture=True)

    monkeypatch.setattr(config_module, "get_config", lambda key, default=None: False)
    assert "已关闭" in await commands.handle_self_opt("/self-opt status", capture=True)

    printed = await commands.handle_self_opt("/self-opt status", capture=False)
    assert printed is None


def test_status_list_show_and_state_changes(capsys) -> None:
    _Store.records = {"p1": _record(), "p2": {**_record("failed"), "id": "p2"}}
    commands.cmd_self_opt_status()
    commands.cmd_self_opt_proposals()
    commands.cmd_self_opt_proposals("approved")
    commands.cmd_self_opt_show("missing")
    commands.cmd_self_opt_show("p1")
    commands.cmd_self_opt_approve("missing")
    commands.cmd_self_opt_approve("p2")
    commands.cmd_self_opt_approve("p1")
    assert _Store.records["p1"]["status"] == "approved"
    _Store.records["p1"]["status"] = "pending"
    _Store.update_result = False
    commands.cmd_self_opt_reject("p1")

    output = capsys.readouterr().out
    assert "待执行提案" in output
    assert "提案详情" in output
    assert "文件变更" in output and "测试用例" in output
    assert "不存在" in output and "无法批准" in output and "拒绝失败" in output


@pytest.mark.asyncio
async def test_apply_proposal_safety_and_result_variants(capsys) -> None:
    await commands.cmd_self_opt_apply("missing")
    _Store.records = {"p1": _record("completed")}
    await commands.cmd_self_opt_apply("p1")
    _Store.records["p1"] = _record(risk="high")
    await commands.cmd_self_opt_apply("p1")

    _Store.records["p1"] = _record("approved", risk="high")
    await commands.cmd_self_opt_apply("p1", root="repo")
    _Store.apply_result = SimpleNamespace(status="skipped", changes_applied=0, error="skip")
    await commands.cmd_self_opt_apply("p1")
    _Store.apply_result = SimpleNamespace(status="failed", changes_applied=0, error="boom")
    await commands.cmd_self_opt_apply("p1")

    output = capsys.readouterr().out
    assert "需先批准" in output
    assert "执行成功" in output
    assert "跳过执行" in output
    assert "执行失败" in output


def test_analyze_enabled_disabled_and_generated(_patch_dependencies, monkeypatch, capsys) -> None:
    env = _patch_dependencies
    commands.cmd_self_opt_analyze()
    assert "无需生成" in capsys.readouterr().out

    monkeypatch.setattr(
        env.generator_module,
        "ProposalGenerator",
        lambda: SimpleNamespace(generate_and_save=lambda: ["p1", "p2"]),
    )
    commands.cmd_self_opt_analyze()
    assert "生成 2 个" in capsys.readouterr().out

    env.values["self_optimization.runtime_analysis_enabled"] = False
    env.values["self_optimization.code_analysis_enabled"] = False
    commands.cmd_self_opt_analyze()
    assert "均已关闭" in capsys.readouterr().out


def test_report_missing_invalid_and_complete(_patch_dependencies, capsys) -> None:
    reports = _patch_dependencies.tmp_path / "reports"
    reports.mkdir()
    commands.cmd_self_opt_report("2026-01-01")
    assert "报告不存在" in capsys.readouterr().out

    (reports / "runtime-2026-01-02.json").write_text("bad", encoding="utf-8")
    commands.cmd_self_opt_report("2026-01-02")
    assert "读取报告失败" in capsys.readouterr().out

    report = {
        "summary": "ok",
        "trace_events_count": 10,
        "sessions_count": 2,
        "tools": {
            "tools": {"exec": {"count": 2, "avg_ms": 50, "success_rate": 0.5}},
            "slow_tools": [{"name": "exec", "avg_ms": 50}],
            "failed_tools": [{"name": "exec", "success_rate": 0.5}],
        },
        "llm": {"request_count": 1, "total_tokens": {"prompt": 3, "completion": 4}},
        "issues": [{"type": "slow", "severity": 3, "tool": "exec"}],
    }
    (reports / "runtime-2026-01-03.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    commands.cmd_self_opt_report("2026-01-03")
    output = capsys.readouterr().out
    assert "运行分析报告" in output
    assert "工具统计" in output and "LLM 统计" in output and "发现问题" in output
