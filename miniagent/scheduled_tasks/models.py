"""定时任务领域模型：调度规格、会话绑定与可序列化的任务实体。

与 :mod:`miniagent.scheduled_tasks.store` 的 JSON 结构一一对应；字段变更需同步迁移读写逻辑。

用户可见字段语义见 ``docs/USER_GUIDE.md``（定时任务）。"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

_logger = logging.getLogger(__name__)

ScheduleKind = Literal["interval", "once", "cron"]
SessionMode = Literal["primary", "ephemeral", "fixed"]

_VALID_SCHEDULE_KINDS = frozenset({"interval", "once", "cron"})
_VALID_SESSION_MODES = frozenset({"primary", "ephemeral", "fixed"})


@dataclass
class ScheduleSpec:
    """单次或周期触发的时刻规则。"""

    kind: ScheduleKind = "interval"
    #: interval 模式：两次触发之间的秒数
    interval_seconds: int | None = None
    #: once 模式：ISO8601（可含 Z 或偏移）；与 timezone 联用见 store 计算逻辑
    once_at_iso: str | None = None
    #: cron 模式：标准 5 段 Unix cron（分 时 日 月 周）
    cron_expr: str | None = None
    #: IANA 名称，如 UTC、Asia/Shanghai；用于解析 naive 的 once_at 与 cron 墙钟
    timezone: str = "UTC"


@dataclass
class SessionSpec:
    """任务跑在哪条会话/通道上（主会话、固定 ID、或每次新建 ephemeral）。"""

    mode: SessionMode = "primary"
    #: mode=fixed 时的会话 ID（如 default、feishu:oc_xxx）
    session_id: str | None = None
    #: 飞书发消息 API 的 receive_id；群聊可从 feishu: 前缀推断，私聊等可显式填写
    feishu_chat_id: str | None = None


@dataclass
class ScheduledTask:
    """一条可持久化的定时 Agent 任务（触发时向队列投递与聊天等价的 prompt）。

    运行态字段由 ticker / store 维护：

    - ``run_count``：每次实际进入执行并完成 finalize（含 ``agent_error``）时递增；
      ``skipped`` / ``cancelled`` 不计数。
    - ``last_error``：最近一次 Agent 失败摘要；成功或 dispatch 退避时按 finalize 规则更新。
    """

    id: str
    name: str
    prompt: str
    enabled: bool = True
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    session: SessionSpec = field(default_factory=SessionSpec)
    #: 下次触发 unix 时间戳（秒）；由调度器维护
    next_run_at: float | None = None
    last_run_at: float | None = None
    run_count: int = 0
    last_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """转为可写入 ``tasks.json`` 的纯 dict 结构。"""
        return asdict(self)

    @staticmethod
    def from_json(data: dict[str, Any]) -> ScheduledTask:
        """从磁盘/工具入参 dict 解析；缺省字段使用安全默认值。

        未知的 ``schedule.kind`` / ``session.mode`` 会记录 warning 并回退为
        ``interval`` / ``primary``。
        """
        sch = data.get("schedule") or {}
        sess = data.get("session") or {}
        raw_kind = sch.get("kind", "interval")
        kind: ScheduleKind = "interval"
        if raw_kind in _VALID_SCHEDULE_KINDS:
            kind = raw_kind  # type: ignore[assignment]
        elif raw_kind is not None:
            _logger.warning(
                "定时任务 %s 含未知 schedule.kind=%r，回退为 interval",
                data.get("id", "?"),
                raw_kind,
            )
        raw_mode = sess.get("mode", "primary")
        mode: SessionMode = "primary"
        if raw_mode in _VALID_SESSION_MODES:
            mode = raw_mode  # type: ignore[assignment]
        elif raw_mode is not None:
            _logger.warning(
                "定时任务 %s 含未知 session.mode=%r，回退为 primary",
                data.get("id", "?"),
                raw_mode,
            )
        return ScheduledTask(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            prompt=str(data.get("prompt") or ""),
            enabled=bool(data.get("enabled", True)),
            schedule=ScheduleSpec(
                kind=kind,
                interval_seconds=sch.get("interval_seconds"),
                once_at_iso=sch.get("once_at_iso"),
                cron_expr=sch.get("cron_expr"),
                timezone=str(sch.get("timezone") or "UTC"),
            ),
            session=SessionSpec(
                mode=mode,
                session_id=sess.get("session_id"),
                feishu_chat_id=sess.get("feishu_chat_id"),
            ),
            next_run_at=data.get("next_run_at"),
            last_run_at=data.get("last_run_at"),
            run_count=int(data.get("run_count") or 0),
            last_error=data.get("last_error"),
        )
