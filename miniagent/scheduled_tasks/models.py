"""定时任务领域模型：调度规格、会话绑定与可序列化的任务实体。

与 :mod:`miniagent.scheduled_tasks.store` 的 JSON 结构一一对应；字段变更需同步迁移读写逻辑。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ScheduleKind = Literal["interval", "once"]
SessionMode = Literal["primary", "ephemeral", "fixed"]


@dataclass
class ScheduleSpec:
    """单次或周期触发的时刻规则。"""

    kind: ScheduleKind = "interval"
    #: interval 模式：两次触发之间的秒数
    interval_seconds: int | None = None
    #: once 模式：ISO8601（可含 Z 或偏移）；与 timezone 联用见 store 计算逻辑
    once_at_iso: str | None = None
    #: IANA 名称，如 UTC、Asia/Shanghai；用于解析 naive 的 once_at
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
    """一条可持久化的定时 Agent 任务（触发时向队列投递与聊天等价的 prompt）。"""

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
        d = asdict(self)
        return d

    @staticmethod
    def from_json(data: dict[str, Any]) -> ScheduledTask:
        """从磁盘/工具入参 dict 解析；缺省字段使用安全默认值。"""
        sch = data.get("schedule") or {}
        sess = data.get("session") or {}
        return ScheduledTask(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            prompt=str(data.get("prompt") or ""),
            enabled=bool(data.get("enabled", True)),
            schedule=ScheduleSpec(
                kind=sch.get("kind", "interval"),
                interval_seconds=sch.get("interval_seconds"),
                once_at_iso=sch.get("once_at_iso"),
                timezone=str(sch.get("timezone") or "UTC"),
            ),
            session=SessionSpec(
                mode=sess.get("mode", "primary"),
                session_id=sess.get("session_id"),
                feishu_chat_id=sess.get("feishu_chat_id"),
            ),
            next_run_at=data.get("next_run_at"),
            last_run_at=data.get("last_run_at"),
            run_count=int(data.get("run_count") or 0),
            last_error=data.get("last_error"),
        )
