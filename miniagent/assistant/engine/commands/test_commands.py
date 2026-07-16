"""内置行为评测命令的参数解析处理器。"""

from __future__ import annotations

from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.engine.commands.output import command_writer

_logger = get_logger(__name__)


async def _run_test(
    category: str | None = None,
    name_pattern: str | None = None,
    *,
    mock: bool = True,
    engine: Any = None,
    registry: Any = None,
    monitor: Any = None,
    skill_toolboxes: list[Any] | None = None,
    skill_prompts: str | None = None,
    state: dict[str, Any] | None = None,
    term_write: Any = None,
    capture: bool = False,
) -> str:
    """Execute deterministic samples or the explicitly selected real Agent path."""
    from miniagent.assistant.testing.agent_adapter import build_execute_agent_from_engine
    from miniagent.assistant.testing.test_runner import run_self_test

    write = command_writer(term_write, capture=capture, logger=_logger)
    mode_label = "mock（样本校验）" if mock else "real（真实 Agent）"
    write(f"🧪 正在运行自测 [{mode_label}]...", "ansicyan")
    execute_agent = None
    if not mock:
        if registry is None:
            message = f"{WARNING_PREFIX} 真实模式需要 registry，请在 CLI 主循环中运行 /test run real"
            if capture:
                return message
            write(message, "ansiyellow")
            return ""
        execute_agent = await build_execute_agent_from_engine(
            engine,
            registry=registry,
            monitor=monitor,
            skill_toolboxes=skill_toolboxes,
            skill_prompts=skill_prompts,
            state=state if isinstance(state, dict) else None,
        )
    report = await run_self_test(
        category=category,
        name_pattern=name_pattern,
        term_write=write,
        execute_agent=execute_agent,
        mock=mock,
    )
    if not capture:
        return ""
    lines = [
        f"🧪 自测结果 [{mode_label}]：{report.passed}/{report.total} 通过 ({report.pass_rate:.1%})",
        f"失败: {report.failed}，跳过: {report.skipped}",
        f"执行时间：{report.duration_seconds:.1f}s",
    ]
    if report.failed > 0:
        lines.append("\n失败的测试：")
        lines.extend(
            f"  ✗ {result.sample_name}: {result.error_message}"
            for result in report.results
            if not result.passed and not result.error_message.startswith("跳过:")
        )
    return "\n".join(lines)


def _list_test_samples() -> str:
    """List self-test samples grouped by category."""
    from miniagent.assistant.testing.test_runner import TestRunner

    samples = TestRunner().load_samples()
    if not samples:
        return "📭 暂无测试样本"
    grouped: dict[str, list[Any]] = {}
    for sample in samples:
        grouped.setdefault(sample.category, []).append(sample)
    lines = ["📋 测试样本列表:", ""]
    for category, items in sorted(grouped.items()):
        lines.append(f"  [{category}]")
        for sample in items:
            description = sample.description[:40] if sample.description else sample.input[:40]
            icon = "🔴" if sample.priority == 1 else "🟡" if sample.priority == 2 else "⚪"
            lines.append(f"    {icon} {sample.name}: {description}")
    return "\n".join(lines)


def _get_test_status() -> str:
    """Format the most recently persisted self-test report."""
    from miniagent.assistant.testing.test_runner import TestRunner

    report = TestRunner().get_last_report()
    if not report:
        return "📭 暂无测试记录，请先运行 `/test run`"
    return "\n".join(
        [
            "🧪 最近测试报告：",
            f"  时间：{report.get('timestamp', '未知')}",
            f"  总数：{report.get('total', 0)}",
            f"  通过：{report.get('passed', 0)}",
            f"  失败：{report.get('failed', 0)}",
            f"  跳过：{report.get('skipped', 0)}",
            f"  通过率：{report.get('passed', 0) / max(1, report.get('total', 1)):.1%}",
            f"  执行时长：{report.get('duration_seconds', 0):.1f}s",
        ]
    )


async def handle_test(
    text: str,
    *,
    state: dict[str, Any],
    engine: Any = None,
    registry: Any = None,
    monitor: Any = None,
    skill_toolboxes: list[Any] | None = None,
    skill_prompts: list[str] | None = None,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """运行、列出或查询内置评测样例。"""
    from miniagent.assistant.engine.commands.session_management import format_test_command_usage

    parts = text.split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    runtime = state.get("runtime_ctx")
    if subcommand == "run":
        mode, category, name_pattern = _parse_run_arguments(parts)
        output = await _run_test(
            category=category,
            name_pattern=name_pattern,
            mock=mode != "real",
            engine=engine,
            registry=registry,
            monitor=monitor,
            skill_toolboxes=skill_toolboxes,
            skill_prompts="\n".join(skill_prompts) if skill_prompts else None,
            state=state,
            term_write=getattr(runtime, "cli_transcript_append", None),
            capture=capture,
        )
    elif subcommand == "list":
        output = _list_test_samples()
    elif subcommand == "status":
        output = _get_test_status()
    else:
        output = format_test_command_usage()
    if capture:
        return output
    if output:
        print(output)
    return None


def _parse_run_arguments(parts: list[str]) -> tuple[str, str | None, str | None]:
    """解析 ``/test run [mock|real] [category] [pattern]``。"""
    if len(parts) > 2 and parts[2].lower() in {"mock", "real"}:
        return (
            parts[2].lower(),
            parts[3] if len(parts) > 3 else None,
            parts[4] if len(parts) > 4 else None,
        )
    return (
        "mock",
        parts[2] if len(parts) > 2 else None,
        parts[3] if len(parts) > 3 else None,
    )


__all__ = ["_get_test_status", "_list_test_samples", "_run_test", "handle_test"]
