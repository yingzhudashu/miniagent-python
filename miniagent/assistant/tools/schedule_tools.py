"""结构化定时任务工具：供 Agent 以 JSON 参数增删改查，避免拼写 ``.schedule add`` 行。

持久化与 ``tasks.json`` 格式见 ``miniagent.assistant.scheduled_tasks.store``；用户文档见 ``README``、``docs/USER_GUIDE.md``。
"""

from __future__ import annotations

import json
import time
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult

SCHEDULE_TOOL_NAMES = frozenset({"manage_scheduled_task"})


def _tool_timezone_spec(args: dict[str, Any]) -> str:
    """返回工具调用显式指定的时区，缺省时使用进程调度默认时区。"""
    from miniagent.assistant.scheduled_tasks.timezone_util import default_schedule_timezone

    raw = (args.get("timezone") or "").strip()
    if raw:
        return raw
    return default_schedule_timezone()


def _session_from_tool(
    session_mode: str,
    fixed_session_id: str | None,
) -> Any:
    """将工具 JSON 中的 session 字段映射为 :class:`~miniagent.assistant.scheduled_tasks.models.SessionSpec`。"""
    from miniagent.assistant.scheduled_tasks.models import SessionSpec

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


def _coerce_interval_seconds(raw: Any) -> int:
    """将 interval_seconds 原始值转为正整数；无法解析时返回 0。"""
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _schedule_tool_handlers() -> dict[str, Any]:
    """返回结构化定时任务 action 的处理器表。"""
    return {
        "list": _schedule_tool_list,
        "show": _schedule_tool_show,
        "remove": _schedule_tool_remove,
        "set_enabled": _schedule_tool_set_enabled,
        "add_interval": _schedule_tool_add,
        "add_once": _schedule_tool_add,
        "add_cron": _schedule_tool_add,
        "update": _schedule_tool_update,
    }


def _schedule_tool_list(args: dict[str, Any]) -> ToolResult:
    """列出任务摘要。"""
    del args
    from miniagent.assistant.scheduled_tasks.store import format_next_run_display, load_tasks

    tasks = load_tasks()
    if not tasks:
        return ToolResult(success=True, content="（暂无定时任务）")
    now = time.time()
    lines = ["定时任务:"]
    for task in tasks:
        kind = str(task.schedule.kind)
        if kind == "cron" and task.schedule.cron_expr:
            kind = f'cron "{task.schedule.cron_expr}"'
        lines.append(
            f"  • {task.id}  ({task.name})  enabled={task.enabled}  "
            f"{kind}  next={format_next_run_display(task, now_ts=now)}  runs={task.run_count}"
        )
    return ToolResult(success=True, content="\n".join(lines))


def _schedule_tool_show(args: dict[str, Any]) -> ToolResult:
    """显示单个任务 JSON。"""
    from miniagent.assistant.scheduled_tasks.store import load_tasks

    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return ToolResult(success=False, content="show 需要 task_id")
    task = next((item for item in load_tasks() if item.id == task_id), None)
    if task is None:
        return ToolResult(success=False, content=f"未找到任务: {task_id}")
    return ToolResult(
        success=True,
        content=json.dumps(task.to_json(), ensure_ascii=False, indent=2),
    )


def _schedule_tool_remove(args: dict[str, Any]) -> ToolResult:
    """删除指定任务。"""
    from miniagent.assistant.scheduled_tasks.store import load_tasks, save_tasks

    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return ToolResult(success=False, content="remove 需要 task_id")
    tasks = load_tasks()
    remaining = [item for item in tasks if item.id != task_id]
    if len(remaining) == len(tasks):
        return ToolResult(success=False, content=f"未找到任务: {task_id}")
    save_tasks(remaining)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 已删除任务 {task_id}")


def _schedule_tool_set_enabled(args: dict[str, Any]) -> ToolResult:
    """设置任务启用状态并修复下次触发时间。"""
    from miniagent.assistant.scheduled_tasks.store import (
        compute_initial_next_run,
        load_tasks,
        repair_invalid_schedules,
        save_tasks,
    )

    task_id = str(args.get("task_id") or "").strip()
    enabled = args.get("enabled")
    if not task_id:
        return ToolResult(success=False, content="set_enabled 需要 task_id")
    if not isinstance(enabled, bool):
        return ToolResult(success=False, content="set_enabled 需要 enabled 布尔值")
    tasks = load_tasks()
    task = next((item for item in tasks if item.id == task_id), None)
    if task is None:
        return ToolResult(success=False, content=f"未找到任务: {task_id}")
    task.enabled = enabled
    if enabled and task.next_run_at is None:
        task.next_run_at = compute_initial_next_run(task)
    repair_invalid_schedules(tasks)
    save_tasks(tasks)
    return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 任务 {task_id} enabled={enabled}")


def _tool_schedule_spec(action: str, args: dict[str, Any], timezone: str) -> Any:
    """构建 add_* 对应 ScheduleSpec；参数错误抛 ``ValueError``。"""
    from miniagent.assistant.scheduled_tasks.cron import validate_cron_expr
    from miniagent.assistant.scheduled_tasks.models import ScheduleSpec

    if action == "add_interval":
        seconds = _coerce_interval_seconds(args.get("interval_seconds"))
        if seconds <= 0:
            raise ValueError("add_interval 需要 task_id、prompt、interval_seconds（正整数）")
        return ScheduleSpec(kind="interval", interval_seconds=seconds, timezone=timezone)
    if action == "add_once":
        once_iso = str(args.get("once_iso") or "").strip()
        if not once_iso:
            raise ValueError("add_once 需要 task_id、prompt、once_iso（ISO8601）")
        return ScheduleSpec(kind="once", once_at_iso=once_iso, timezone=timezone)
    cron_expr = str(args.get("cron_expr") or "").strip()
    if not cron_expr:
        raise ValueError("add_cron 需要 task_id、prompt、cron_expr（5 段 Unix cron）")
    return ScheduleSpec(
        kind="cron",
        cron_expr=validate_cron_expr(cron_expr),
        timezone=timezone,
    )


def _schedule_tool_add(args: dict[str, Any]) -> ToolResult:
    """创建 interval/once/cron 任务并验证首次触发时间。"""
    from miniagent.assistant.scheduled_tasks.models import ScheduledTask
    from miniagent.assistant.scheduled_tasks.store import (
        compute_initial_next_run,
        format_next_run_display,
        load_tasks,
        save_tasks,
    )

    action = str(args.get("action") or "")
    task_id = str(args.get("task_id") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not task_id or not prompt:
        return ToolResult(success=False, content=f"{action} 需要 task_id、prompt")
    try:
        session = _session_from_tool(
            str(args.get("session_mode") or "primary"),
            args.get("fixed_session_id"),
        )
        timezone = _tool_timezone_spec(args)
        schedule = _tool_schedule_spec(action, args, timezone)
    except ValueError as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} {error}")
    task = ScheduledTask(
        id=task_id,
        name=task_id,
        prompt=prompt,
        enabled=True,
        schedule=schedule,
        session=session,
    )
    task.next_run_at = compute_initial_next_run(task)
    if task.next_run_at is None:
        message = "无法解析 once_iso，请使用 ISO8601（可含 Z 或偏移）"
        if action == "add_cron":
            message = "无法根据 cron 计算下次触发时间"
        return ToolResult(success=False, content=message)
    if action == "add_once" and task.next_run_at < time.time():
        return ToolResult(success=False, content="一次性任务时间已在过去")
    tasks = load_tasks()
    if any(item.id == task_id for item in tasks):
        return ToolResult(success=False, content=f"任务 ID 已存在: {task_id}")
    tasks.append(task)
    save_tasks(tasks)
    kind = action.removeprefix("add_")
    return ToolResult(
        success=True,
        content=(
            f"{SUCCESS_PREFIX} 已添加 {kind} 任务 {task_id} timezone={timezone} "
            f"next={format_next_run_display(task)}"
        ),
    )


def _updated_schedule(existing: Any, args: dict[str, Any], timezone: str) -> Any:
    """根据更新参数构建新调度，省略 kind 时保持原类型。"""
    from miniagent.assistant.scheduled_tasks.cron import validate_cron_expr
    from miniagent.assistant.scheduled_tasks.models import ScheduleSpec

    kind = str(args.get("schedule_kind") or existing.schedule.kind).strip().lower()
    if kind == "interval":
        seconds = _coerce_interval_seconds(
            args.get("interval_seconds", existing.schedule.interval_seconds)
        )
        if seconds <= 0:
            raise ValueError("interval 更新需要 interval_seconds（正整数）")
        return ScheduleSpec(kind="interval", interval_seconds=seconds, timezone=timezone)
    if kind == "once":
        once_iso = str(args.get("once_iso") or existing.schedule.once_at_iso or "").strip()
        if not once_iso:
            raise ValueError("once 更新需要 once_iso")
        return ScheduleSpec(kind="once", once_at_iso=once_iso, timezone=timezone)
    if kind == "cron":
        expression = str(args.get("cron_expr") or existing.schedule.cron_expr or "").strip()
        if not expression:
            raise ValueError("cron 更新需要 cron_expr")
        return ScheduleSpec(
            kind="cron",
            cron_expr=validate_cron_expr(expression),
            timezone=timezone,
        )
    raise ValueError(f"未知 schedule_kind: {kind}")


def _schedule_tool_update(args: dict[str, Any]) -> ToolResult:
    """更新现有任务并重新计算下一次触发时间。"""
    from miniagent.assistant.scheduled_tasks.store import (
        compute_initial_next_run,
        format_next_run_display,
        load_tasks,
        repair_invalid_schedules,
        save_tasks,
    )

    task_id = str(args.get("task_id") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not task_id or not prompt:
        return ToolResult(success=False, content="update 需要 task_id、prompt")
    tasks = load_tasks()
    existing = next((item for item in tasks if item.id == task_id), None)
    if existing is None:
        return ToolResult(success=False, content=f"未找到任务: {task_id}")
    timezone = str(args.get("timezone") or existing.schedule.timezone or "").strip()
    timezone = timezone or _tool_timezone_spec(args)
    try:
        existing.session = _session_from_tool(
            str(args.get("session_mode") or existing.session.mode),
            args.get("fixed_session_id") or existing.session.session_id,
        )
        existing.schedule = _updated_schedule(existing, args, timezone)
    except ValueError as error:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} {error}")
    existing.prompt = prompt
    existing.enabled = True
    existing.last_error = None
    existing.next_run_at = compute_initial_next_run(existing)
    if existing.next_run_at is None:
        return ToolResult(success=False, content="无法计算下次触发时间")
    repair_invalid_schedules(tasks)
    save_tasks(tasks)
    return ToolResult(
        success=True,
        content=(
            f"{SUCCESS_PREFIX} 已更新 {task_id} timezone={existing.schedule.timezone} "
            f"next={format_next_run_display(existing)}"
        ),
    )


async def _manage_scheduled_task_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """``manage_scheduled_task``：JSON 驱动定时任务 CRUD；飞书等非 CLI 渠道禁止写操作。"""
    action = (args.get("action") or "").strip().lower()
    if not action:
        return ToolResult(success=False, content="缺少 action")

    read_only = action in ("list", "show")
    if not read_only and not ctx.cli_dispatch_allow_mutations:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} 当前渠道不允许修改定时任务（飞书场景）；请在本地 CLI 使用或使用 list/show。",
        )

    handler = _schedule_tool_handlers().get(action)
    if handler is None:
        return ToolResult(
            success=False,
            content=(
                f"未知 action: {action}；可用 list、show、add_interval、add_once、add_cron、"
                "update、remove、set_enabled"
            ),
        )
    return handler(args)


_manage_scheduled_task_schema = {
    "type": "function",
    "function": {
        "name": "manage_scheduled_task",
        "description": (
            "以结构化参数管理持久化定时任务（MINIAGENT_PATHS_STATE_DIR/scheduled_tasks/tasks.json），"
            "不依赖易碎的 .schedule 行格式。飞书默认仅 list/show；MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时与 CLI 可增删改。"
            "与 run_dot_command 的 .schedule 操作同一套存储。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "list | show | align_tz | add_interval | add_once | add_cron | "
                        "update | remove | set_enabled"
                    ),
                },
                "task_id": {
                    "type": "string",
                    "description": "任务 id（add_* / update / remove / show / set_enabled）",
                },
                "prompt": {
                    "type": "string",
                    "description": "add_* / update：注入 Agent 的用户提示正文",
                },
                "schedule_kind": {
                    "type": "string",
                    "description": "update：interval | once | cron；省略则保持原 kind",
                },
                "interval_seconds": {
                    "type": "integer",
                    "description": "add_interval：触发间隔秒数",
                },
                "once_iso": {
                    "type": "string",
                    "description": "add_once：ISO8601，可含 Z 或 +08:00；无时区时可配 timezone",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "add_cron：标准 5 段 Unix cron，如 10 8 * * *",
                },
                "timezone": {
                    "type": "string",
                    "description": "add_* / update：IANA 时区，默认 UTC",
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
