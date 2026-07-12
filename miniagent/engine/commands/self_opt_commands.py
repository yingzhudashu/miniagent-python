"""自我优化命令处理器。

本模块只负责把自优化领域服务转换为 CLI/飞书可捕获的文本输出。提案的
持久化、风险校验和实际应用仍由 ``core.self_opt`` 负责，避免命令聚合模块
同时承担领域逻辑与展示逻辑。
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any, cast

from miniagent.infrastructure.trace_events import ProposalStatus
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def _capture_output(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """捕获自优化叶子命令的 print 输出并统一错误映射。"""
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            result = callable_(*args, **kwargs)
    except Exception as error:
        return f"{ERROR_PREFIX} 命令执行失败: {error}"
    return str(result) if isinstance(result, str) else buffer.getvalue().strip()


async def handle_self_opt(
    text: str,
    *,
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """解析并执行一个自优化子命令。"""
    from miniagent.core.constants import CLI_SELF_OPT_TOOLS
    from miniagent.infrastructure.json_config import get_config

    if not CLI_SELF_OPT_TOOLS or not get_config("self_optimization.enabled", True):
        output = f"{WARNING_PREFIX} 自我优化功能已关闭（self_optimization.enabled）"
    else:
        parts = text.split()
        subcommand = parts[1].lower() if len(parts) > 1 else ""
        output = await _dispatch_self_opt_subcommand(parts, subcommand)
    if capture:
        return output
    print(output)
    return None


async def _dispatch_self_opt_subcommand(parts: list[str], subcommand: str) -> str:
    """执行已启用的自优化子命令，保持参数校验集中且可测试。"""
    if subcommand in {"", "status"}:
        return _capture_output(cmd_self_opt_status)
    if subcommand == "proposals":
        status = parts[2] if len(parts) > 2 else None
        return _capture_output(cmd_self_opt_proposals, status=status)
    if subcommand in {"show", "approve", "reject"}:
        if len(parts) < 3:
            return f"用法: /self-opt {subcommand} <id>"
        command = {
            "show": cmd_self_opt_show,
            "approve": cmd_self_opt_approve,
            "reject": cmd_self_opt_reject,
        }[subcommand]
        return _capture_output(command, parts[2])
    if subcommand == "apply":
        if len(parts) < 3:
            return "用法: /self-opt apply <id> [root]"
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                await cmd_self_opt_apply(
                    parts[2], root=parts[3] if len(parts) > 3 else ""
                )
        except Exception as error:
            return f"{ERROR_PREFIX} 命令执行失败: {error}"
        return buffer.getvalue().strip()
    if subcommand == "analyze":
        return _capture_output(cmd_self_opt_analyze)
    if subcommand == "report":
        return _capture_output(cmd_self_opt_report, date=parts[2] if len(parts) > 2 else None)
    return (
        f"{WARNING_PREFIX} 未知的子命令: {subcommand}\n"
        "用法: /self-opt status|proposals|show|approve|reject|apply|analyze|report"
    )


def cmd_self_opt_status() -> None:
    """打印自我优化开关、提案目录及待处理数量。"""
    from miniagent.core.self_opt.proposal_store import ProposalStore, get_proposal_output_dir
    from miniagent.infrastructure.json_config import get_config

    enabled = get_config("self_optimization.enabled", True)
    auto_apply = get_config("self_optimization.auto_apply", False)
    max_risk = get_config("self_optimization.auto_apply_max_risk", "low")
    runtime_enabled = get_config("self_optimization.runtime_analysis_enabled", True)
    code_enabled = get_config("self_optimization.code_analysis_enabled", True)
    proposals = ProposalStore().load_proposals()
    pending_count = sum(p.get("status") == "pending" for p in proposals)

    print("\n🔧 自我优化系统状态:")
    print(f"  系统启用: {'✅ 是' if enabled else '❌ 否'}")
    print(f"  自动执行: {'✅ 是' if auto_apply else '❌ 否（仅生成提案）'}")
    print(f"  自动执行风险上限: {max_risk}")
    print(f"  运行日志分析: {'✅ 启用' if runtime_enabled else '❌ 禁用'}")
    print(f"  代码静态分析: {'✅ 启用' if code_enabled else '❌ 禁用'}")
    print(f"  提案存储路径: {get_proposal_output_dir()}")
    print(f"  待执行提案: {pending_count} 个")
    print()


def cmd_self_opt_proposals(status: str | None = None) -> None:
    """按可选状态过滤器打印提案列表。"""
    from miniagent.core.self_opt.proposal_store import ProposalStore

    proposals = ProposalStore().load_proposals(
        status=cast(ProposalStatus | None, status)
    )
    if not proposals:
        print(f"\n📭 {status or '全部'}提案: 暂无\n")
        return

    print(f"\n📋 提案列表 ({status or '全部'}):\n")
    status_icons = {
        "pending": "⏳",
        "approved": SUCCESS_PREFIX,
        "rejected": ERROR_PREFIX,
        "executing": "🔄",
        "completed": "🎉",
        "failed": WARNING_PREFIX,
    }
    for record in proposals:
        proposal = record.get("proposal", {})
        icon = status_icons.get(record.get("status", "pending"), "❓")
        print(f"  {icon} {record.get('id', '?')}")
        print(f"     来源: {record.get('source', '?')}, 风险: {proposal.get('risk_level', 'low')}")
        print(f"     描述: {proposal.get('description', '')[:50]}...")
        print(
            f"     状态: {record.get('status', 'pending')}, "
            f"工时: {proposal.get('estimated_effort', 0)}min"
        )
        print()
    print(f"总计: {len(proposals)} 个提案\n")


def cmd_self_opt_show(proposal_id: str) -> None:
    """打印指定提案的详情、文件变更和测试用例。"""
    from miniagent.core.self_opt.proposal_store import ProposalStore

    record = ProposalStore().get_proposal(proposal_id)
    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return

    proposal = record.get("proposal", {})
    print(f"\n📄 提案详情: {proposal_id}\n")
    print(f"  状态: {record.get('status', 'pending')}")
    print(f"  来源: {record.get('source', '?')}")
    print(f"  创建时间: {record.get('created_at', '?')}")
    print(f"  更新时间: {record.get('updated_at', '?')}")
    print()
    print(f"  类型: {proposal.get('type', '?')}")
    print(f"  风险等级: {proposal.get('risk_level', 'low')}")
    print(f"  目标: {proposal.get('target', '')}")
    print(f"  描述: {proposal.get('description', '')}")
    print()
    print(f"  理由: {proposal.get('rationale', '')}")
    print(f"  预期收益: {proposal.get('expected_benefit', '')}")
    print(f"  预估工时: {proposal.get('estimated_effort', 0)} 分钟")
    print()

    files = proposal.get("files", [])
    if files:
        print("  文件变更:")
        for file_change in files:
            print(f"    - {file_change.get('action', '?')}: {file_change.get('path', '')}")
            if file_change.get("reason"):
                print(f"      原因: {file_change.get('reason')}")
        print()

    test_cases = proposal.get("test_cases", [])
    if test_cases:
        print("  测试用例:")
        for case in test_cases:
            print(f"    - {case.get('id', '?')}: {case.get('description', '')}")
        print()


def _set_proposal_status(
    proposal_id: str, target: ProposalStatus, action: str
) -> None:
    """把待处理提案切换到批准或拒绝状态，并打印稳定的用户文案。"""
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    record = store.get_proposal(proposal_id)
    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return
    current_status = record.get("status", "pending")
    if current_status != "pending":
        print(f"\n{WARNING_PREFIX} 提案当前状态为 {current_status}，无法{action}\n")
        return
    if store.update_status(proposal_id, target):
        print(f"\n{SUCCESS_PREFIX} 提案 {proposal_id} 已{action}\n")
    else:
        print(f"\n{ERROR_PREFIX} {action}失败\n")


def cmd_self_opt_approve(proposal_id: str) -> None:
    """批准一个仍处于 pending 状态的提案。"""
    _set_proposal_status(proposal_id, "approved", "批准")


def cmd_self_opt_reject(proposal_id: str) -> None:
    """拒绝一个仍处于 pending 状态的提案。"""
    _set_proposal_status(proposal_id, "rejected", "拒绝")


async def cmd_self_opt_apply(proposal_id: str, root: str = "") -> None:
    """手工应用提案；高风险提案必须先显式批准。"""
    from miniagent.core.self_opt.proposal_store import ProposalStore

    store = ProposalStore()
    record = store.get_proposal(proposal_id)
    if not record:
        print(f"\n{ERROR_PREFIX} 提案 {proposal_id} 不存在\n")
        return
    current_status = record.get("status", "pending")
    if current_status not in ("pending", "approved"):
        print(f"\n{WARNING_PREFIX} 提案当前状态为 {current_status}，无法执行\n")
        return
    if record.get("proposal", {}).get("risk_level", "low") == "high" and current_status != "approved":
        print(f"\n{WARNING_PREFIX} 高风险提案需先批准后再执行")
        print(f"  请先执行: /self-opt approve {proposal_id}\n")
        return

    print(f"\n🔄 正在执行提案 {proposal_id}...\n")
    result = await store.apply_proposal_async(proposal_id, root=root, manual=True)
    if result.status == "success":
        print(f"{SUCCESS_PREFIX} 提案执行成功")
        print(f"  应用变更: {result.changes_applied} 个\n")
    elif result.status == "skipped":
        print(f"{WARNING_PREFIX} 提案跳过执行: {result.error}\n")
    else:
        print(f"{ERROR_PREFIX} 提案执行失败: {result.error}\n")


def cmd_self_opt_analyze() -> None:
    """运行已启用的分析器并保存生成的优化提案。"""
    from miniagent.core.self_opt.proposal_generator import ProposalGenerator
    from miniagent.infrastructure.json_config import get_config

    runtime_on = get_config("self_optimization.runtime_analysis_enabled", True)
    code_on = get_config("self_optimization.code_analysis_enabled", True)
    sources = [name for enabled, name in ((runtime_on, "运行日志"), (code_on, "代码静态")) if enabled]
    print(f"\n🔍 正在分析（{' + '.join(sources) if sources else '（无分析源启用）'}）...\n")
    if not sources:
        print(f"{WARNING_PREFIX} runtime_analysis_enabled 与 code_analysis_enabled 均已关闭\n")
        return

    saved_ids = ProposalGenerator().generate_and_save()
    if saved_ids:
        print(f"{SUCCESS_PREFIX} 生成 {len(saved_ids)} 个优化提案:\n")
        for proposal_id in saved_ids:
            print(f"  - {proposal_id}")
        print()
    else:
        print("📭 未发现问题，无需生成提案\n")


def cmd_self_opt_report(date: str | None = None) -> None:
    """打印指定 UTC 日期的运行分析报告；日期缺省为今天。"""
    import json
    from datetime import datetime, timezone

    from miniagent.core.self_opt.proposal_store import get_reports_dir

    report_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_file = get_reports_dir() / f"runtime-{report_date}.json"
    if not report_file.exists():
        print(f"\n{WARNING_PREFIX} 报告不存在: {report_date}\n")
        return
    try:
        with report_file.open("r", encoding="utf-8") as file:
            report = json.load(file)
    except (OSError, ValueError, TypeError) as exc:
        print(f"\n{ERROR_PREFIX} 读取报告失败: {exc}\n")
        return

    print(f"\n📊 运行分析报告: {report_date}\n")
    print(f"  摘要: {report.get('summary', '无')}")
    print(f"  Trace 事件数: {report.get('trace_events_count', 0)}")
    print(f"  会话数: {report.get('sessions_count', 0)}")
    print()
    tools = report.get("tools", {})
    tool_stats = tools.get("tools", {})
    if tool_stats:
        print("  工具统计:")
        ordered = sorted(tool_stats.items(), key=lambda item: item[1].get("avg_ms", 0), reverse=True)
        for name, stats in ordered[:5]:
            print(
                f"    - {name}: {stats.get('count', 0)}次, 平均{stats.get('avg_ms', 0)}ms, "
                f"成功率{stats.get('success_rate', 1):.1%}"
            )
        print()
    for title, key, formatter in (
        ("  ⚠️ 慢工具:", "slow_tools", lambda item: f"    - {item.get('name')}: 平均 {item.get('avg_ms')}ms"),
        ("  ❌ 失败率高工具:", "failed_tools", lambda item: f"    - {item.get('name')}: 成功率 {item.get('success_rate', 0):.1%}"),
    ):
        entries = tools.get(key, [])
        if entries:
            print(title)
            for entry in entries:
                print(formatter(entry))
            print()
    llm = report.get("llm", {})
    if llm.get("request_count"):
        tokens = llm.get("total_tokens", {})
        print("  LLM 统计:")
        print(f"    - 请求次数: {llm.get('request_count', 0)}")
        print(f"    - 总 tokens: prompt={tokens.get('prompt', 0)}, completion={tokens.get('completion', 0)}")
        print()
    issues = report.get("issues", [])
    if issues:
        print("  🔧 发现问题:")
        for issue in issues:
            icon = "🔴" if issue.get("severity", 1) >= 3 else "🟡"
            subject = issue.get("tool") or issue.get("error_type") or ""
            print(f"    {icon} [{issue.get('type')}] {subject}")
        print()


__all__ = [
    "handle_self_opt",
    "cmd_self_opt_analyze",
    "cmd_self_opt_apply",
    "cmd_self_opt_approve",
    "cmd_self_opt_proposals",
    "cmd_self_opt_reject",
    "cmd_self_opt_report",
    "cmd_self_opt_show",
    "cmd_self_opt_status",
]
