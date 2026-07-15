"""提案持久化存储。

将 OptimizationProposal 写入 JSONL 文件，支持状态管理、查询、更新。
默认输出目录：workspaces/self_opt/proposals/

文件结构：
- proposals-{YYYY-MM-DD}.jsonl：每日提案追加写入
- history.json：提案索引（id、状态、来源、时间戳）

提案状态流转：
pending -> approved -> executing -> completed/failed
pending -> rejected

使用方式：
    store = ProposalStore()

    # 创建提案
    proposal = OptimizationProposal(...)
    store.save_proposal(proposal)

    # 查询待执行提案
    pending = store.load_proposals(status="pending")

    # 批准提案
    store.update_status(proposal.id, "approved")

    # 手动执行提案（manual=True 绕过 auto_apply 配置）
    result = await store.apply_proposal_async(proposal.id, manual=True)

详见 docs/SELF_OPT.md。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.observability import emit_trace
from miniagent.agent.trace_events import (
    EVENT_PROPOSAL_APPLY,
    EVENT_PROPOSAL_APPROVE,
    EVENT_PROPOSAL_CREATE,
    EVENT_PROPOSAL_REJECT,
    ProposalSource,
    ProposalStatus,
    RiskLevel,
    make_proposal_event,
)
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.infrastructure.persistence import dump_state_file, load_state_file
from miniagent.assistant.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.assistant.self_opt.auto_optimizer import apply_proposal
from miniagent.assistant.self_opt.types import OptimizationProposal, OptimizationResult

_logger = get_logger(__name__)
install_builtin_state_schemas()


def get_proposal_output_dir() -> Path:
    """获取提案输出目录。

    优先级：
    1. 配置 self_optimization.proposal_output_dir
    2. 默认 workspaces/self_opt/proposals
    """
    config_dir = get_config("self_optimization.proposal_output_dir", None)
    if config_dir:
        return Path(config_dir)
    return Path("workspaces/self_opt/proposals")


def get_proposal_file(date: str | None = None) -> Path:
    """获取指定日期的提案文件路径。

    Args:
        date: 日期字符串（YYYY-MM-DD），默认今天

    Returns:
        提案文件路径（proposals-{YYYY-MM-DD}.jsonl）
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir = get_proposal_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"proposals-{date}.jsonl"


def get_history_file() -> Path:
    """获取提案索引文件路径（history.json）。"""
    return get_proposal_output_dir() / "history.json"


def get_reports_dir() -> Path:
    """获取分析报告目录。

    Returns:
        报告目录路径（workspaces/self_opt/reports）
    """
    output_dir = get_proposal_output_dir().parent / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _update_history_index(record: dict[str, Any]) -> None:
    """更新 history.json 提案索引（按 id 去重，保留最新状态）。"""
    history_file = get_history_file()
    index: list[dict[str, Any]] = []
    if history_file.exists():
        try:
            loaded = load_state_file("self_opt_proposal_index", history_file)
            entries = loaded.get("entries", [])
            if isinstance(entries, list):
                index = [item for item in entries if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            index = []

    proposal_data = record.get("proposal", {})
    entry = {
        "id": record.get("id"),
        "status": record.get("status"),
        "source": record.get("source"),
        "target": proposal_data.get("target", ""),
        "risk_level": proposal_data.get("risk_level", "low"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }

    index = [e for e in index if e.get("id") != entry["id"]]
    index.append(entry)
    index.sort(key=lambda e: e.get("updated_at", ""), reverse=True)

    try:
        dump_state_file(
            "self_opt_proposal_index",
            history_file,
            {"entries": index},
        )
    except OSError as e:
        _logger.warning("更新提案索引失败: %s", e)


class ProposalStore:
    """提案持久化存储管理器。

    功能：
    - 保存提案到 JSONL 文件
    - 状态管理与更新
    - 提案查询与列表
    - 提案执行与结果记录
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        """初始化提案存储。

        Args:
            output_dir: 输出目录（默认配置值）
        """
        self._output_dir = output_dir or get_proposal_output_dir()
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _iter_proposal_files(self, date: str | None = None) -> list[Path]:
        """列出要读取的提案 JSONL 文件（date 为 None 时读取全部历史）。"""
        if date is not None:
            proposal_file = get_proposal_file(date)
            return [proposal_file] if proposal_file.exists() else []
        files = sorted(self._output_dir.glob("proposals-*.jsonl"), reverse=True)
        return files

    def _find_proposal_file(self, proposal_id: str) -> Path | None:
        """定位包含指定提案 ID 的 JSONL 文件。"""
        for proposal_file in self._iter_proposal_files():
            try:
                with proposal_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            if record.get("id") == proposal_id:
                                return proposal_file
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
        return None

    def save_proposal(
        self,
        proposal: OptimizationProposal,
        source: ProposalSource = "runtime_analysis",
    ) -> str:
        """保存提案到文件。

        自动添加状态、来源、时间戳等元数据。
        发出 trace 事件通知。

        Args:
            proposal: 优化提案
            source: 提案来源

        Returns:
            提案 ID
        """
        # 构建存储记录
        record = {
            "id": proposal.id,
            "status": "pending",
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "proposal": {
                "id": proposal.id,
                "type": proposal.type,
                "risk_level": proposal.risk_level,
                "target": proposal.target,
                "description": proposal.description,
                "rationale": proposal.rationale,
                "expected_benefit": proposal.expected_benefit,
                "estimated_effort": proposal.estimated_effort,
                "files": [
                    {
                        "path": f.path,
                        "action": f.action,
                        "content": f.content,
                        "reason": f.reason,
                    }
                    for f in proposal.files
                ],
                "test_cases": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "description": tc.description,
                        "command": tc.command,
                    }
                    for tc in proposal.test_cases
                ],
            },
        }

        # 写入文件
        proposal_file = get_proposal_file()
        line = json.dumps(record, ensure_ascii=False)
        with proposal_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        _update_history_index(record)

        # 发出 trace 事件
        emit_trace(
            make_proposal_event(
                event_type=EVENT_PROPOSAL_CREATE,
                proposal_id=proposal.id,
                source=source,
                risk_level=proposal.risk_level,
                description=proposal.description,
            )
        )

        _logger.info("提案已保存: %s (%s)", proposal.id, source)
        return proposal.id

    def load_proposals(
        self,
        status: ProposalStatus | None = None,
        source: ProposalSource | None = None,
        risk_level: RiskLevel | None = None,
        date: str | None = None,
    ) -> list[dict[str, Any]]:
        """加载提案列表，支持多维度过滤。

        Args:
            status: 按状态过滤
            source: 按来源过滤
            risk_level: 按风险等级过滤
            date: 限定单日；None 表示加载全部历史文件

        Returns:
            提案记录列表（按 updated_at 降序）
        """
        proposals: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for proposal_file in self._iter_proposal_files(date):
            try:
                with proposal_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            record_id = record.get("id", "")
                            if record_id in seen_ids:
                                continue
                            # 过滤条件
                            if status and record.get("status") != status:
                                continue
                            if source and record.get("source") != source:
                                continue
                            if risk_level and record.get("proposal", {}).get("risk_level") != risk_level:
                                continue
                            seen_ids.add(record_id)
                            proposals.append(record)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue

        proposals.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return proposals

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """获取指定提案。

        Args:
            proposal_id: 提案 ID

        Returns:
            提案记录（如果存在）
        """
        for record in self.load_proposals():
            if record.get("id") == proposal_id:
                return record
        return None

    def update_status(
        self,
        proposal_id: str,
        new_status: ProposalStatus,
        result: str | None = None,
    ) -> bool:
        """更新提案状态。

        Args:
            proposal_id: 提案 ID
            new_status: 新状态
            result: 执行结果（可选）

        Returns:
            是否成功更新
        """
        proposal_file = self._find_proposal_file(proposal_id)
        if proposal_file is None:
            return False

        updated = False
        lines: list[str] = []
        updated_record: dict[str, Any] | None = None

        try:
            with proposal_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("id") == proposal_id:
                            record["status"] = new_status
                            record["updated_at"] = datetime.now(timezone.utc).isoformat()
                            if result:
                                record["result"] = result
                            updated = True
                            updated_record = record

                            # 发出 trace 事件
                            event_type = {
                                "approved": EVENT_PROPOSAL_APPROVE,
                                "rejected": EVENT_PROPOSAL_REJECT,
                                "completed": EVENT_PROPOSAL_APPLY,
                                "failed": EVENT_PROPOSAL_APPLY,
                            }.get(new_status)
                            if event_type:
                                emit_trace(
                                    make_proposal_event(
                                        event_type=event_type,
                                        proposal_id=proposal_id,
                                        source=record.get("source", "runtime_analysis"),
                                        risk_level=record.get("proposal", {}).get("risk_level", "low"),
                                        result=result,
                                    )
                                )
                        lines.append(json.dumps(record, ensure_ascii=False))
                    except json.JSONDecodeError:
                        continue

            if updated:
                with proposal_file.open("w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                if updated_record:
                    _update_history_index(updated_record)
                _logger.info("提案状态已更新: %s -> %s", proposal_id, new_status)

        except OSError as e:
            _logger.error("更新提案状态失败: %s", e)
            return False

        return updated

    @staticmethod
    def _proposal_apply_denial(
        proposal_id: str,
        record: dict[str, Any],
        *,
        dry_run: bool,
        manual: bool,
    ) -> OptimizationResult | None:
        """校验自动执行开关和风险上限；允许执行时返回 ``None``。"""
        if dry_run or manual:
            return None
        if not get_config("self_optimization.auto_apply", False):
            return OptimizationResult(
                proposal_id=proposal_id,
                status="skipped",
                error="auto_apply 未启用，请使用 /self-opt apply 手动执行",
            )
        max_risk = get_config("self_optimization.auto_apply_max_risk", "low")
        proposal_risk = record.get("proposal", {}).get("risk_level", "low")
        risk_order = {"low": 0, "medium": 1, "high": 2}
        if risk_order.get(proposal_risk, 0) <= risk_order.get(max_risk, 0):
            return None
        return OptimizationResult(
            proposal_id=proposal_id,
            status="skipped",
            error=f"风险等级 {proposal_risk} 超过配置上限 {max_risk}",
        )

    @staticmethod
    def _proposal_from_record(proposal_id: str, record: dict[str, Any]) -> OptimizationProposal:
        """从 JSONL 记录重建强类型优化提案。"""
        from miniagent.assistant.self_opt.types import FileChange, OptTestCase

        data = record.get("proposal", {})
        return OptimizationProposal(
            id=data.get("id", proposal_id),
            type=data.get("type", "optimize"),
            risk_level=data.get("risk_level", "low"),
            target=data.get("target", ""),
            description=data.get("description", ""),
            rationale=data.get("rationale", ""),
            expected_benefit=data.get("expected_benefit", ""),
            estimated_effort=data.get("estimated_effort", 0),
            files=[
                FileChange(
                    path=item.get("path", ""),
                    action=item.get("action", "update"),
                    content=item.get("content", ""),
                    reason=item.get("reason", ""),
                )
                for item in data.get("files", [])
            ],
            test_cases=[
                OptTestCase(
                    id=item.get("id", ""),
                    type=item.get("type", "unit"),
                    description=item.get("description", ""),
                    command=item.get("command", ""),
                )
                for item in data.get("test_cases", [])
            ],
        )

    async def apply_proposal_async(
        self,
        proposal_id: str,
        root: str = "",
        auto_rollback: bool = True,
        dry_run: bool = False,
        manual: bool = False,
    ) -> OptimizationResult:
        """异步执行提案。

        Args:
            proposal_id: 提案 ID
            root: 项目根目录
            auto_rollback: 失败时自动回滚
            dry_run: 仅模拟执行
            manual: 手动执行（CLI /self-opt apply），绕过 auto_apply 配置

        Returns:
            执行结果
        """
        record = self.get_proposal(proposal_id)
        if not record:
            return OptimizationResult(
                proposal_id=proposal_id,
                status="skipped",
                error="提案不存在",
            )

        current_status = record.get("status", "pending")
        denial = self._proposal_apply_denial(
            proposal_id, record, dry_run=dry_run, manual=manual
        )
        if denial is not None:
            return denial

        allow_high_risk = manual and current_status == "approved"

        # 更新状态为 executing
        self.update_status(proposal_id, "executing")

        proposal = self._proposal_from_record(proposal_id, record)

        # 执行提案
        result = await apply_proposal(
            proposal,
            root=root,
            auto_rollback=auto_rollback,
            dry_run=dry_run,
            allow_high_risk=allow_high_risk,
        )

        # 更新最终状态
        if result.status == "success":
            final_status: ProposalStatus = "completed"
        elif result.status == "skipped":
            final_status = current_status if current_status in ("pending", "approved") else "failed"
        else:
            final_status = "failed"

        if result.status != "skipped":
            self.update_status(
                proposal_id,
                final_status,
                result=result.error or "成功",
            )

        return result

    @staticmethod
    def cleanup_old_proposals(retention_days: int = 30) -> int:
        """清理过期提案文件。

        Args:
            retention_days: 保留天数

        Returns:
            删除的文件数
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted = 0

        for proposal_file in get_proposal_output_dir().glob("proposals-*.jsonl"):
            try:
                date_str = proposal_file.stem.replace("proposals-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff_date:
                    proposal_file.unlink()
                    deleted += 1
            except (ValueError, OSError):
                continue

        if deleted > 0:
            _logger.info("清理过期提案文件: %d 个", deleted)

        return deleted


__all__ = [
    "get_proposal_output_dir",
    "get_proposal_file",
    "get_history_file",
    "get_reports_dir",
    "ProposalStore",
]
