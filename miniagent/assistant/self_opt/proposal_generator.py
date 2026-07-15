"""运行日志驱动的提案生成器。

从 RuntimeAnalyzer 的分析报告中识别问题，
生成结构化的 OptimizationProposal。
支持与代码静态分析提案合并、优先级排序。

生成逻辑：
1. 慢工具 -> 性能优化提案
2. 高失败率工具 -> 工具修复提案
3. 高频错误 -> 错误处理优化提案
4. LLM token 消耗过大 -> 提示词优化提案
5. 循环检测 -> 行为优化提案

提案风险等级：
- low：配置调整、参数优化
- medium：代码修改、工具改进
- high：架构调整、重构

使用方式：
    generator = ProposalGenerator()
    proposals = generator.generate_from_runtime_report(report)

    # 合并代码分析提案
    all_proposals = generator.merge_proposals(
        runtime_proposals,
        code_proposals,
    )

详见 docs/SELF_OPT.md（运行日志驱动提案）。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.trace_events import RiskLevel
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.self_opt.proposal_store import ProposalStore
from miniagent.assistant.self_opt.runtime_analyzer import RuntimeAnalyzer
from miniagent.assistant.self_opt.types import (
    OptimizationProposal,
    OptTestCase,
)

_logger = get_logger(__name__)


def _generate_proposal_id() -> str:
    """生成唯一提案 ID。"""
    return f"opt-{uuid.uuid4().hex[:8]}"


class ProposalGenerator:
    """运行日志驱动的提案生成器。

    从运行分析报告识别问题并生成提案。
    """

    def __init__(self) -> None:
        """初始化生成器。"""
        pass

    def generate_from_runtime_report(
        self,
        report: dict[str, Any],
        max_proposals: int = 10,
    ) -> list[OptimizationProposal]:
        """从运行分析报告生成提案。

        Args:
            report: RuntimeAnalyzer 生成的分析报告
            max_proposals: 最大提案数量

        Returns:
            优化提案列表（按优先级排序）
        """
        proposals = self._proposals_from_issues(report.get("issues", []))

        # 2. LLM token 消耗提案
        llm_stats = report.get("llm", {})
        if llm_stats.get("request_count", 0) > 10:
            total_tokens = llm_stats.get("total_tokens", {})
            total = total_tokens.get("prompt", 0) + total_tokens.get("completion", 0)
            if total > 100000:
                proposals.append(self._make_token_optimization_proposal(llm_stats))

        # 按风险等级和预估工时排序
        risk_order = {"low": 0, "medium": 1, "high": 2}
        proposals.sort(key=lambda p: (risk_order.get(p.risk_level, 1), p.estimated_effort))

        return proposals[:max_proposals]

    def _proposals_from_issues(self, issues: list[dict[str, Any]]) -> list[OptimizationProposal]:
        """按问题类型映射生成器，未知类型被安全忽略。"""
        factories = {
            "slow_tool": self._make_slow_tool_proposal,
            "tool_failure": self._make_tool_failure_proposal,
            "high_frequency_error": self._make_error_handling_proposal,
            "tool_loop": self._make_tool_loop_proposal,
            "ping_pong": self._make_ping_pong_proposal,
            "context_pressure": self._make_context_pressure_proposal,
        }
        proposals: list[OptimizationProposal] = []
        for issue in issues:
            factory = factories.get(str(issue.get("type", "")))
            proposal = factory(issue) if factory else None
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def _make_slow_tool_proposal(
        self,
        issue: dict[str, Any],
    ) -> OptimizationProposal | None:
        """生成慢工具优化提案。

        Args:
            issue: 慢工具问题

        Returns:
            优化提案
        """
        tool_name = issue.get("tool", "")
        avg_ms = issue.get("avg_ms", 0)

        threshold = get_config("self_optimization.min_duration_ms_threshold", 2000)

        if not tool_name or avg_ms < threshold:
            return None

        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="optimize",
            risk_level="medium",
            target=f"工具: {tool_name}",
            description=f"工具 {tool_name} 平均执行时延 {avg_ms}ms，超过阈值 {threshold}ms",
            rationale=(
                "时延过高可能影响用户体验和系统响应速度。"
                "可能原因：网络延迟、数据处理效率、依赖服务响应慢。"
            ),
            expected_benefit="降低平均执行时延，提升用户体验",
            estimated_effort=30,
            test_cases=[
                OptTestCase(
                    id=f"tc-{tool_name}-perf",
                    type="integration",
                    description=f"验证 {tool_name} 执行时延",
                    command="python -c 'import time; start=time.time(); pass; print(f\"duration: {time.time()-start}s\")'",
                    expected="时延 < 阈值",
                )
            ],
        )

    def _make_tool_failure_proposal(
        self,
        issue: dict[str, Any],
    ) -> OptimizationProposal | None:
        """生成工具失败修复提案。

        Args:
            issue: 工具失败问题

        Returns:
            优化提案
        """
        tool_name = issue.get("tool", "")
        success_rate = issue.get("success_rate", 1.0)

        if not tool_name or success_rate > 0.90:
            return None

        threshold = get_config("self_optimization.min_failure_rate_threshold", 0.05)

        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="refactor",
            risk_level="high",
            target=f"工具: {tool_name}",
            description=f"工具 {tool_name} 成功率仅 {success_rate:.1%}，失败率超过阈值 {threshold:.1%}",
            rationale=(
                "高失败率可能源于参数校验不完善、错误处理缺失、依赖服务不稳定。"
                "需要排查错误日志，定位根因。"
            ),
            expected_benefit="提升工具稳定性，减少用户误用导致的失败",
            estimated_effort=60,
            test_cases=[
                OptTestCase(
                    id=f"tc-{tool_name}-stability",
                    type="integration",
                    description=f"验证 {tool_name} 稳定性",
                    command="pytest tests/test_tools.py -k {tool_name}",
                    expected="成功率 > 95%",
                )
            ],
        )

    def _make_error_handling_proposal(
        self,
        issue: dict[str, Any],
    ) -> OptimizationProposal | None:
        """生成错误处理优化提案。

        Args:
            issue: 高频错误问题

        Returns:
            优化提案
        """
        error_type = issue.get("error_type", "")
        count = issue.get("count", 0)
        is_user_error = issue.get("is_user_error", False)

        if not error_type or count < 3:
            return None

        risk: RiskLevel = "low" if is_user_error else "medium"

        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="optimize",
            risk_level=risk,
            target=f"错误处理: {error_type}",
            description=f"错误类型 {error_type} 出现 {count} 次",
            rationale=(
                f"{'用户误用' if is_user_error else '工具缺陷'}导致的错误。"
                f"{'需要改进错误提示或参数校验。' if is_user_error else '需要排查错误根因并修复。'}"
            ),
            expected_benefit="减少错误发生频率，提升用户体验",
            estimated_effort=15 if is_user_error else 30,
        )

    def _make_token_optimization_proposal(
        self,
        llm_stats: dict[str, Any],
    ) -> OptimizationProposal:
        """生成 token 优化提案。

        Args:
            llm_stats: LLM 统计数据

        Returns:
            优化提案
        """
        request_count = llm_stats.get("request_count", 0)
        total_tokens = llm_stats.get("total_tokens", {})
        prompt_tokens = total_tokens.get("prompt", 0)
        completion_tokens = total_tokens.get("completion", 0)
        avg_prompt = prompt_tokens / max(request_count, 1)

        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="optimize",
            risk_level="low",
            target="LLM token 消耗",
            description=(
                f"LLM token 消耗过大：prompt {prompt_tokens}, completion {completion_tokens}。"
                f"平均每轮 prompt tokens: {avg_prompt:.0f}"
            ),
            rationale=(
                "高 token 消耗增加 API 成本，可能导致上下文溢出。"
                "优化方向：精简提示词、减少历史消息注入、启用压缩策略。"
            ),
            expected_benefit="降低 API 成本，减少上下文压力",
            estimated_effort=20,
        )

    def _make_tool_loop_proposal(
        self,
        issue: dict[str, Any],
    ) -> OptimizationProposal | None:
        """生成工具重复调用循环提案。"""
        tool_name = issue.get("tool", "")
        count = issue.get("count", 0)
        if not tool_name or count < 5:
            return None

        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="optimize",
            risk_level="medium",
            target=f"行为: {tool_name} 重复调用",
            description=f"工具 {tool_name} 在同一会话中被调用 {count} 次，可能存在无效循环",
            rationale="重复调用浪费 token 与时间，应检查 Agent 提示词或工具返回是否导致反复调用。",
            expected_benefit="减少无效工具调用，提升响应效率",
            estimated_effort=25,
        )

    def _make_ping_pong_proposal(
        self,
        issue: dict[str, Any],
    ) -> OptimizationProposal | None:
        """生成 ping-pong 交替调用提案。"""
        tools = issue.get("tools", [])
        if len(tools) < 2:
            return None

        a, b = tools[0], tools[1]
        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="optimize",
            risk_level="medium",
            target=f"行为: {a} ↔ {b} ping-pong",
            description=f"检测到 {a} 与 {b} 交替调用模式",
            rationale="Ping-pong 模式通常表示 Agent 在两个工具间反复切换无法收敛，需优化策略或错误处理。",
            expected_benefit="打破无效循环，减少 token 消耗",
            estimated_effort=30,
        )

    def _make_context_pressure_proposal(
        self,
        issue: dict[str, Any],
    ) -> OptimizationProposal | None:
        """生成上下文压缩压力提案。"""
        compress_count = issue.get("compress_count", 0)
        if compress_count < 5:
            return None

        return OptimizationProposal(
            id=_generate_proposal_id(),
            type="optimize",
            risk_level="low",
            target="上下文压缩频率",
            description=f"上下文压缩触发 {compress_count} 次，上下文压力较大",
            rationale="频繁压缩说明对话上下文接近窗口上限，可精简系统提示或启用更积极的截断策略。",
            expected_benefit="降低压缩频率，保留更多有效上下文",
            estimated_effort=20,
        )

    def merge_proposals(
        self,
        runtime_proposals: list[OptimizationProposal],
        code_proposals: list[OptimizationProposal],
        max_total: int = 20,
    ) -> list[OptimizationProposal]:
        """合并运行分析提案和代码分析提案。

        按优先级排序，去重（相同 target 的提案合并）。

        Args:
            runtime_proposals: 运行分析提案
            code_proposals: 代码静态分析提案
            max_total: 最大总数

        Returns:
            合并后的提案列表
        """
        risk_order = {"low": 0, "medium": 1, "high": 2}

        # 按 target 去重
        seen_targets: dict[str, OptimizationProposal] = {}

        for proposal in runtime_proposals + code_proposals:
            target = proposal.target
            if target in seen_targets:
                # 合并：保留风险更高的
                existing = seen_targets[target]
                if risk_order.get(proposal.risk_level, 0) > risk_order.get(existing.risk_level, 0):
                    seen_targets[target] = proposal
            else:
                seen_targets[target] = proposal

        # 排序
        proposals = list(seen_targets.values())
        proposals.sort(key=lambda p: (risk_order.get(p.risk_level, 1), p.estimated_effort))

        return proposals[:max_total]

    def generate_and_save(
        self,
        date: str | None = None,
        max_proposals: int = 10,
        root: str | None = None,
    ) -> list[str]:
        """生成提案并保存到存储。

        当 ``code_analysis_enabled`` 为 true 时，同时运行代码静态分析并合并提案。

        Args:
            date: 分析日期，默认今天
            max_proposals: 最大提案数量
            root: 代码分析项目根目录（默认 cwd）

        Returns:
            保存的提案 ID 列表
        """
        project_root = root or os.getcwd()

        runtime_proposals: list[OptimizationProposal] = []
        if get_config("self_optimization.runtime_analysis_enabled", True):
            analyzer = RuntimeAnalyzer()
            report = analyzer.analyze(date)
            analyzer.save_report(report)
            runtime_proposals = self.generate_from_runtime_report(report, max_proposals)

        code_proposals: list[OptimizationProposal] = []
        if get_config("self_optimization.code_analysis_enabled", True):
            try:
                from miniagent.assistant.self_opt.inspector import inspect_project
                from miniagent.assistant.self_opt.proposal_engine import generate_proposals

                inspection = asyncio.run(inspect_project(project_root))
                code_proposals = asyncio.run(
                    generate_proposals(
                        inspection,
                        root=project_root,
                        max_proposals=max_proposals,
                    )
                )
            except Exception as e:
                _logger.warning("代码静态分析失败: %s", e)

        # 按 target 去重后保存（运行分析优先）
        saved_ids: list[str] = []
        seen_targets: set[str] = set()
        store = ProposalStore()

        for proposal in runtime_proposals:
            if proposal.target in seen_targets:
                continue
            if len(saved_ids) >= max_proposals:
                break
            try:
                proposal_id = store.save_proposal(proposal, source="runtime_analysis")
                saved_ids.append(proposal_id)
                seen_targets.add(proposal.target)
            except Exception as e:
                _logger.warning("保存提案失败: %s", e)

        for proposal in code_proposals:
            if proposal.target in seen_targets:
                continue
            if len(saved_ids) >= max_proposals:
                break
            try:
                proposal_id = store.save_proposal(proposal, source="code_analysis")
                saved_ids.append(proposal_id)
                seen_targets.add(proposal.target)
            except Exception as e:
                _logger.warning("保存代码分析提案失败: %s", e)

        _logger.info("生成并保存 %d 个优化提案", len(saved_ids))
        return saved_ids


__all__ = [
    "ProposalGenerator",
]
