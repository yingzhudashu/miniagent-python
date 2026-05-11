"""结构化定时任务工具：供 Agent 以 JSON 参数增删改查，避免拼写 ``.schedule add`` 行。"""

from __future__ import annotations

import json
import time
from typing import Any

from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

SCHEDULE_TOOL_NAMES = frozenset({"manage_scheduled_task"})


def _session_from_tool(
    session_mode: str,
    fixed_session_id: str | None,
) -> Any:
    """将工具 JSON 中的 session 字段映射为 :class:`~miniagent.scheduled_tasks.models.SessionSpec`。"""
    from miniagent.scheduled_tasks.models import SessionSpec

    m = (session_mode or "primary").strip().lower()
    if m == "primary":
        return SessionSpec(mode="primary")
    if m == "ephemeral":
        return SessionSpec(mode="ephemeral")
    if m == "fixed":
        sid = (fixed_session_id or "").strip()
        if not sid:
            raise ValueError("session_mode=fixed 时必须提供 fixed_session_id")
        fc: str | None = None
        if sid.startswith("feishu:"):
            fc = sid[7:].strip() or None
        return SessionSpec(mode="fixed", session_id=sid, feishu_chat_id=fc)
    raise ValueError(f"未知 session_mode: {session_mode!r}")


async def _manage_scheduled_task_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from miniagent.scheduled_tasks.models import ScheduledTask, ScheduleSpec
    from miniagent.scheduled_tasks.store import compute_initial_next_run, load_tasks, save_tasks

    action = (args.get("action") or "").strip().lower()
    if not action:
        return ToolResult(success=False, content="缺少 action")

    read_only = action in ("list", "show")
    if not read_only and not ctx.cli_dispatch_allow_mutations:
        return ToolResult(
            success=False,
            content="⚠️ 当前渠道不允许修改定时任务（飞书场景）；请在本地 CLI 使用或使用 list/show。",
        )

    if action == "list":
        tasks = load_tasks()
        if not tasks:
            return ToolResult(success=True, content="（暂无定时任务）")
        lines = ["定时任务:"]
        for t in tasks:
            nxt = t.next_run_at
            nxt_s = f"{nxt:.0f}" if nxt is not None else "-"
            lines.append(
                f"  • {t.id}  ({t.name})  enabled={t.enabled}  "
                f"{t.schedule.kind}  next={nxt_s}  runs={t.run_count}"
            )
        return ToolResult(success=True, content="\n".join(lines))

    if action == "show":
        tid = (args.get("task_id") or "").strip()
        if not tid:
            return ToolResult(success=False, content="show 需要 task_id")
        for t in load_tasks():
            if t.id == tid:
                return ToolResult(
                    success=True,
                    content=json.dumps(t.to_json(), ensure_ascii=False, indent=2),
                )
        return ToolResult(success=False, content=f"未找到任务: {tid}")

    if action == "remove":
        tid = (args.get("task_id") or "").strip()
        if not tid:
            return ToolResult(success=False, content="remove 需要 task_id")
        tasks = load_tasks()
        new = [x for x in tasks if x.id != tid]
        if len(new) == len(tasks):
            return ToolResult(success=False, content=f"未找到任务: {tid}")
        save_tasks(new)
        return ToolResult(success=True, content=f"✅ 已删除任务 {tid}")

    if action == "set_enabled":
        tid = (args.get("task_id") or "").strip()
        if not tid:
            return ToolResult(success=False, content="set_enabled 需要 task_id")
        en = args.get("enabled")
        if not isinstance(en, bool):
            return ToolResult(success=False, content="set_enabled 需要 enabled 布尔值")
        tasks = load_tasks()
        for t in tasks:
            if t.id == tid:
                t.enabled = en
                if en and t.next_run_at is None:
                    t.next_run_at = compute_initial_next_run(t)
                save_tasks(tasks)
                return ToolResult(success=True, content=f"✅ 任务 {tid} enabled={en}")
        return ToolResult(success=False, content=f"未找到任务: {tid}")

    if action == "add_interval":
        tid = (args.get("task_id") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        raw_sec = args.get("interval_seconds")
        try:
            sec = int(raw_sec) if raw_sec is not None else 0
        except (TypeError, ValueError):
            sec = 0
        if not tid or not prompt or sec <= 0:
            return ToolResult(
                success=False,
                content="add_interval 需要 task_id、prompt、interval_seconds（正整数）",
            )
        try:
            sess = _session_from_tool(
                str(args.get("session_mode") or "primary"),
                args.get("fixed_session_id"),
            )
        except ValueError as e:
            return ToolResult(success=False, content=f"❌ {e}")
        tz = (args.get("timezone") or "UTC").strip() or "UTC"
        task = ScheduledTask(
            id=tid,
            name=tid,
            prompt=prompt,
            enabled=True,
            schedule=ScheduleSpec(kind="interval", interval_seconds=sec, timezone=tz),
            session=sess,
        )
        task.next_run_at = compute_initial_next_run(task)
        tasks = load_tasks()
        if any(x.id == tid for x in tasks):
            return ToolResult(success=False, content=f"任务 ID 已存在: {tid}")
        tasks.append(task)
        save_tasks(tasks)
        return ToolResult(success=True, content=f"✅ 已添加 interval 任务 {tid} next_run_at={task.next_run_at}")

    if action == "add_once":
        tid = (args.get("task_id") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        iso = (args.get("once_iso") or "").strip()
        if not tid or not prompt or not iso:
            return ToolResult(
                success=False,
                content="add_once 需要 task_id、prompt、once_iso（ISO8601）",
            )
        try:
            sess = _session_from_tool(
                str(args.get("session_mode") or "primary"),
                args.get("fixed_session_id"),
            )
        except ValueError as e:
            return ToolResult(success=False, content=f"❌ {e}")
        tz = (args.get("timezone") or "UTC").strip() or "UTC"
        task = ScheduledTask(
            id=tid,
            name=tid,
            prompt=prompt,
            enabled=True,
            schedule=ScheduleSpec(kind="once", once_at_iso=iso, timezone=tz),
            session=sess,
        )
        task.next_run_at = compute_initial_next_run(task)
        if task.next_run_at is None:
            return ToolResult(success=False, content="无法解析 once_iso，请使用 ISO8601（可含 Z 或偏移）")
        if task.next_run_at < time.time():
            return ToolResult(success=False, content="一次性任务时间已在过去")
        tasks = load_tasks()
        if any(x.id == tid for x in tasks):
            return ToolResult(success=False, content=f"任务 ID 已存在: {tid}")
        tasks.append(task)
        save_tasks(tasks)
        return ToolResult(success=True, content=f"✅ 已添加 once 任务 {tid} next_run_at={task.next_run_at}")

    return ToolResult(
        success=False,
        content=f"未知 action: {action}；可用 list、show、add_interval、add_once、remove、set_enabled",
    )


_manage_scheduled_task_schema = {
    "type": "function",
    "function": {
        "name": "manage_scheduled_task",
        "description": (
            "以结构化参数管理持久化定时任务（MINI_AGENT_STATE/scheduled_tasks/tasks.json），"
            "不依赖易碎的 .schedule 行格式。飞书场景下仅 list/show 可用；本地 CLI 可增删改。"
            "与 run_dot_command 的 .schedule 操作同一套存储。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "list | show | add_interval | add_once | remove | set_enabled"
                    ),
                },
                "task_id": {"type": "string", "description": "任务 id（add_* / remove / show / set_enabled）"},
                "prompt": {"type": "string", "description": "add_* 时注入 Agent 的用户提示正文"},
                "interval_seconds": {
                    "type": "integer",
                    "description": "add_interval：触发间隔秒数",
                },
                "once_iso": {
                    "type": "string",
                    "description": "add_once：ISO8601，可含 Z 或 +08:00；无时区时可配 timezone",
                },
                "timezone": {
                    "type": "string",
                    "description": "add_once 解析 naive 时间用 IANA 时区，默认 UTC",
                },
                "session_mode": {
                    "type": "string",
                    "description": "primary | ephemeral | fixed；fixed 时需 fixed_session_id",
                },
                "fixed_session_id": {
                    "type": "string",
                    "description": "如 default 或 feishu:oc_xxx",
                },
                "enabled": {"type": "boolean", "description": "set_enabled 专用"},
            },
            "required": ["action"],
        },
    },
}

schedule_tools: dict[str, ToolDefinition] = {
    "manage_scheduled_task": ToolDefinition(
        schema=_manage_scheduled_task_schema,
        handler=_manage_scheduled_task_handler,
        permission="allowlist",
        help_text="结构化增删改查定时任务（与 .schedule 共用存储）",
        toolbox="miniagent_shell",
    ),
}

__all__ = ["schedule_tools", "SCHEDULE_TOOL_NAMES"]
