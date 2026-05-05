"""Confirmation Manager — 确认管理器

基于风险的确认策略。

风险等级与确认策略：
- low: 自动执行，无需确认
- medium: 批量确认（展示所有 medium 风险提案，用户一次性确认）
- high: 逐个确认（每个提案单独确认）
- destructive: 强制确认（必须明确同意）

设计原则：
- 风险越低，自动化程度越高
- 用户始终对高风险操作有最终决定权
- 支持异步回调机制
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from .types import OptimizationProposal, RiskLevel

# 确认回调函数类型
ConfirmationCallback = Callable[[str, OptimizationProposal], Awaitable[bool]]


@dataclass
class ConfirmationRequest:
    """确认请求。"""
    proposal: OptimizationProposal
    reason: str
    requires_action: bool = True
    confirmed: bool = False


class ConfirmationManager:
    """管理用户确认流程。

    根据提案风险等级决定是否请求确认。
    """

    def __init__(
        self,
        auto_approve_low: bool = True,
        batch_medium: bool = True,
        callback: ConfirmationCallback | None = None,
    ):
        """初始化确认管理器。

        Args:
            auto_approve_low: 是否自动批准低风险提案。
            batch_medium: 是否批量确认中风险提案。
            callback: 确认回调函数 (proposal_id, proposal) -> bool。
        """
        self.auto_approve_low = auto_approve_low
        self.batch_medium = batch_medium
        self.callback = callback
        self._pending: list[ConfirmationRequest] = []

    def needs_confirmation(self, proposal: OptimizationProposal) -> bool:
        """判断提案是否需要用户确认。

        Args:
            proposal: 优化提案。

        Returns:
            是否需要确认。
        """
        risk = proposal.risk_level
        if risk == "low" and self.auto_approve_low:
            return False
        return True

    def _get_confirmation_reason(self, proposal: OptimizationProposal) -> str:
        """生成确认原因说明。"""
        risk = proposal.risk_level
        reasons = {
            "medium": "此提案涉及中等风险改动，可能影响现有功能",
            "high": "此提案涉及高风险改动，可能破坏现有架构",
            "destructive": "此提案涉及破坏性改动，可能导致数据丢失",
        }
        return reasons.get(risk, "此提案需要确认")

    async def request_confirmation(
        self,
        proposal: OptimizationProposal,
    ) -> bool:
        """请求用户确认提案。

        流程：
        1. 检查是否需要确认
        2. 如果需要，调用回调函数或等待用户输入
        3. 返回确认结果

        Args:
            proposal: 优化提案。

        Returns:
            是否已确认。
        """
        if not self.needs_confirmation(proposal):
            return True  # 自动批准

        reason = self._get_confirmation_reason(proposal)
        req = ConfirmationRequest(
            proposal=proposal,
            reason=reason,
        )

        # 如果有回调，使用回调
        if self.callback:
            try:
                confirmed = await self.callback(proposal.id, proposal)
                req.confirmed = confirmed
                return confirmed
            except Exception as e:
                print(f"[confirmation-manager] Callback error: {e}")
                return False

        # 否则在控制台等待用户输入
        return await self._console_confirm(proposal, reason)

    async def request_batch_confirmation(
        self,
        proposals: list[OptimizationProposal],
    ) -> list[tuple[OptimizationProposal, bool]]:
        """批量请求确认（仅适用于 medium 风险）。

        Args:
            proposals: 提案列表。

        Returns:
            列表 of (提案, 是否确认)。
        """
        results: list[tuple[OptimizationProposal, bool]] = []

        medium_proposals = [
            p for p in proposals if p.risk_level == "medium"
        ]
        others = [
            p for p in proposals if p.risk_level != "medium"
        ]

        # 批量确认 medium 风险
        if medium_proposals and self.batch_medium:
            print(f"\n=== 批量确认 {len(medium_proposals)} 个中等风险提案 ===")
            for i, p in enumerate(medium_proposals, 1):
                print(f"  {i}. [{p.risk_level}] {p.description}")

            if self.callback:
                # 回调模式下，逐个确认
                for p in medium_proposals:
                    confirmed = await self.request_confirmation(p)
                    results.append((p, confirmed))
            else:
                # 控制台模式，一次性确认所有
                confirmed = await self._console_batch_confirm(medium_proposals)
                for p in medium_proposals:
                    results.append((p, confirmed))

        # 非 medium 风险逐个确认
        for p in others:
            if self.needs_confirmation(p):
                confirmed = await self.request_confirmation(p)
                results.append((p, confirmed))
            else:
                results.append((p, True))

        return results

    async def _console_confirm(
        self,
        proposal: OptimizationProposal,
        reason: str,
    ) -> bool:
        """在控制台等待用户确认。"""
        print(f"\n--- 确认请求 ---")
        print(f"提案: {proposal.description}")
        print(f"风险: {proposal.risk_level}")
        print(f"原因: {reason}")
        print(f"文件变更: {len(proposal.files)} 个文件")
        print(f"测试用例: {len(proposal.test_cases)} 个")
        print("-" * 30)

        try:
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(
                None,
                lambda: input("是否确认? (y/n): ").strip().lower(),
            )
            return answer in ("y", "yes")
        except Exception:
            return False

    async def _console_batch_confirm(
        self,
        proposals: list[OptimizationProposal],
    ) -> bool:
        """在控制台批量确认。"""
        try:
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(
                None,
                lambda: input(f"确认所有 {len(proposals)} 个提案? (y/n): ").strip().lower(),
            )
            return answer in ("y", "yes")
        except Exception:
            return False
