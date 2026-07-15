"""定时任务命令的解析、校验与持久化协调。"""

from __future__ import annotations

import json
import shlex
import time
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def format_schedule_command_usage() -> str:
    """返回 ``/schedule`` 子命令的用法说明文本（终端与工具复用）。"""
    return (
        "定时任务（持久化在 MINIAGENT_PATHS_STATE_DIR/scheduled_tasks/，经消息队列跑 Agent）：\n"
        "  /schedule list\n"
        "  /schedule show <id>\n"
        "  /schedule remove <id>\n"
        "  /schedule enable <id>  |  /schedule disable <id>\n"
        "  /schedule update <id> every|once|cron ...（语法同 add，不含新建 id） [--tz IANA] -- <prompt>\n"
        "  /schedule add <id> every <秒> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>\n"
        "  /schedule add <id> once <ISO8601> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>\n"
        '  /schedule add <id> cron "<分> <时> <日> <月> <周>" <primary|...> [--tz IANA] -- <prompt>\n'
        "  说明: 用 `` -- `` 分隔参数区与 prompt；cron 为标准 5 段 Unix 表达式（半角 *）。\n"
        "  关闭调度: 环境变量 MINIAGENT_DISABLE_SCHEDULED_TASKS=1"
    )


def _schedule_head_strip_tz_tokens(tokens: list[str]) -> tuple[list[str], str | None]:
    """从参数列表去掉 ``--tz X``，返回新列表与时区。"""
    tz_override: str | None = None
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "--tz" and i + 1 < len(tokens):
            tz_override = tokens[i + 1].strip() or "UTC"
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out, tz_override


def _resolve_schedule_tz(
    tz_override: str | None,
    *,
    existing: Any | None = None,
) -> str:
    """``add`` 用 env 默认；``update`` 未写 ``--tz`` 时保留原任务时区。"""
    from miniagent.assistant.scheduled_tasks.timezone_util import default_schedule_timezone

    if tz_override is not None:
        return tz_override
    if existing is not None:
        return (existing.schedule.timezone or "").strip() or default_schedule_timezone()
    return default_schedule_timezone()


def _parse_cron_add_tokens(tokens: list[str]) -> tuple[str, str]:
    """从 ``add <id> cron … <session>`` 的 token 列表解析 cron 与会话 token。"""
    if len(tokens) < 4 or tokens[0].lower() != "add" or tokens[2].lower() != "cron":
        raise ValueError("cron 参数不足")
    rest = tokens[3:]
    if len(rest) < 2:
        raise ValueError("cron 须为 5 段（分 时 日 月 周）及会话说明")
    sess_token = rest[-1]
    cron_parts = rest[:-1]
    if len(cron_parts) == 1:
        expr = cron_parts[0]
    elif len(cron_parts) == 5:
        expr = " ".join(cron_parts)
    else:
        raise ValueError("cron 须为 5 段（分 时 日 月 周），或使用引号包裹整段表达式")
    return expr, sess_token


def _parse_schedule_session_spec(token: str) -> Any:
    """解析 ``add`` 子命令中的会话目标 token，返回 :class:`~miniagent.assistant.scheduled_tasks.models.SessionSpec`。"""
    from miniagent.assistant.scheduled_tasks.models import SessionSpec

    t = token.strip()
    if t == "primary":
        return SessionSpec(mode="primary")
    if t == "ephemeral":
        return SessionSpec(mode="ephemeral")
    if t.startswith("fixed:"):
        sid = t[6:].strip()
        if not sid:
            raise ValueError("fixed: 后须填写会话 ID（如 default 或 feishu:oc_xxx）")
        feishu_chat: str | None = None
        if sid.startswith("feishu:"):
            feishu_chat = sid[7:].strip() or None
        return SessionSpec(mode="fixed", session_id=sid, feishu_chat_id=feishu_chat)
    raise ValueError(f"未知会话说明 {token!r}，须为 primary / ephemeral / fixed:...")


def _schedule_list() -> str:
    """格式化全部定时任务，错误摘要压缩为单行。"""
    from miniagent.assistant.scheduled_tasks.store import format_next_run_display, load_tasks

    tasks = load_tasks()
    if not tasks:
        return "（暂无定时任务）"
    lines = ["定时任务:"]
    now = time.time()
    for task in tasks:
        display_kind = str(task.schedule.kind)
        if display_kind == "cron" and task.schedule.cron_expr:
            display_kind = f'cron "{task.schedule.cron_expr}"'
        lines.append(
            f"  • {task.id}  ({task.name})  enabled={task.enabled}  "
            f"{display_kind}  next={format_next_run_display(task, now_ts=now)}  "
            f"runs={task.run_count}"
        )
        if task.last_error:
            lines.append(f"      err: {task.last_error.replace(chr(10), ' ')[:160]}")
    return "\n".join(lines)


def _schedule_show(task_id: str) -> str:
    """返回单个任务的稳定 JSON 表示。"""
    from miniagent.assistant.scheduled_tasks.store import load_tasks

    task = next((item for item in load_tasks() if item.id == task_id), None)
    if task is None:
        return f"未找到任务: {task_id}"
    return json.dumps(task.to_json(), ensure_ascii=False, indent=2)


def _schedule_remove(task_id: str) -> str:
    """删除指定任务；不存在时不改写持久化文件。"""
    from miniagent.assistant.scheduled_tasks.store import load_tasks, save_tasks

    tasks = load_tasks()
    remaining = [task for task in tasks if task.id != task_id]
    if len(remaining) == len(tasks):
        return f"未找到任务: {task_id}"
    save_tasks(remaining)
    return f"{SUCCESS_PREFIX} 已删除任务 {task_id}"


def _schedule_set_enabled(task_id: str, *, enabled: bool) -> str:
    """启停任务，并在重新启用时补算缺失的下次运行时间。"""
    from miniagent.assistant.scheduled_tasks.store import (
        compute_initial_next_run,
        load_tasks,
        repair_invalid_schedules,
        save_tasks,
    )

    tasks = load_tasks()
    task = next((item for item in tasks if item.id == task_id), None)
    if task is None:
        return f"未找到任务: {task_id}"
    task.enabled = enabled
    if enabled and task.next_run_at is None:
        task.next_run_at = compute_initial_next_run(task)
    if enabled:
        repair_invalid_schedules(tasks)
    save_tasks(tasks)
    verb = "启用" if enabled else "禁用"
    return f"{SUCCESS_PREFIX} 已{verb} {task_id}"


def _parse_schedule_mutation(raw: str) -> tuple[list[str], str, str | None] | str:
    """解析 add/update 的参数区、prompt 与可选时区；错误直接返回用户文本。"""
    marker = " -- "
    if marker not in raw:
        return (
            "缺少 `` -- `` 分隔符（用于分隔会话参数与 prompt）。\n"
            + format_schedule_command_usage()
        )
    head, prompt = raw.split(marker, 1)
    prompt = prompt.strip()
    if not prompt:
        return "prompt 不能为空"
    head = head.strip()
    if head.lower().startswith("/schedule"):
        head = head[9:].strip()
    try:
        parts = shlex.split(head)
    except ValueError as error:
        return f"{ERROR_PREFIX} 参数解析失败: {error}"
    parts, timezone = _schedule_head_strip_tz_tokens(parts)
    return parts, prompt, timezone


def _build_schedule(
    *,
    command: str,
    task_id: str,
    kind: str,
    tail: list[str],
    timezone: str,
) -> tuple[Any, Any]:
    """依据 CLI token 构建调度与会话对象；所有格式错误以 ``ValueError`` 返回。"""
    from miniagent.assistant.scheduled_tasks.models import ScheduleSpec

    if kind in {"every", "once"} and len(tail) < 2:
        raise ValueError("参数不足")
    if kind == "every":
        seconds = int(tail[0], 10)
        if seconds <= 0:
            raise ValueError("间隔须为正整数")
        return (
            ScheduleSpec(kind="interval", interval_seconds=seconds, timezone=timezone),
            _parse_schedule_session_spec(tail[1]),
        )
    if kind == "once":
        return (
            ScheduleSpec(kind="once", once_at_iso=tail[0], timezone=timezone),
            _parse_schedule_session_spec(tail[1]),
        )
    if kind == "cron":
        from miniagent.assistant.scheduled_tasks.cron import validate_cron_expr

        expression, session_token = _parse_cron_add_tokens(["add", task_id, "cron", *tail])
        return (
            ScheduleSpec(
                kind="cron",
                cron_expr=validate_cron_expr(expression),
                timezone=timezone,
            ),
            _parse_schedule_session_spec(session_token),
        )
    raise ValueError("调度类型须为 every、once 或 cron")


def _validate_next_run(task: Any, *, new_task: bool) -> str | None:
    """计算下次触发时间，并区分 once 解析、过去时间与通用计算失败。"""
    from miniagent.assistant.scheduled_tasks.store import compute_initial_next_run

    task.next_run_at = compute_initial_next_run(task)
    if task.next_run_at is None:
        if new_task and task.schedule.kind == "once":
            return "无法解析 once 时间，请使用 ISO8601（可含 Z 或 +08:00）"
        if new_task and task.schedule.kind == "cron":
            return "无法根据 cron 计算下次触发时间"
        return "无法计算下次触发时间（请检查调度参数）"
    if new_task and task.schedule.kind == "once" and task.next_run_at < time.time():
        return "一次性任务时间已在过去，请使用未来时间"
    return None


def _schedule_add(raw: str) -> str:
    """解析、验证并持久化一个新任务。"""
    from miniagent.assistant.scheduled_tasks.models import ScheduledTask
    from miniagent.assistant.scheduled_tasks.store import (
        format_next_run_display,
        load_tasks,
        save_tasks,
    )

    parsed = _parse_schedule_mutation(raw)
    if isinstance(parsed, str):
        return parsed
    parts, prompt, timezone_override = parsed
    if len(parts) < 4 or parts[0].lower() != "add":
        return "参数不足。\n" + format_schedule_command_usage()
    task_id, kind = parts[1], parts[2].lower()
    try:
        schedule, session = _build_schedule(
            command="add",
            task_id=task_id,
            kind=kind,
            tail=parts[3:],
            timezone=_resolve_schedule_tz(timezone_override),
        )
    except ValueError as error:
        message = str(error)
        if message == "参数不足":
            return "参数不足。\n" + format_schedule_command_usage()
        if message.startswith("调度类型"):
            return message + "。\n" + format_schedule_command_usage()
        return f"{ERROR_PREFIX} {message}"
    task = ScheduledTask(
        id=task_id,
        name=task_id,
        prompt=prompt,
        enabled=True,
        schedule=schedule,
        session=session,
    )
    validation_error = _validate_next_run(task, new_task=True)
    if validation_error:
        return validation_error
    tasks = load_tasks()
    if any(item.id == task_id for item in tasks):
        return f"任务 ID 已存在: {task_id}"
    tasks.append(task)
    save_tasks(tasks)
    return (
        f"{SUCCESS_PREFIX} 已添加任务 {task_id}，timezone={task.schedule.timezone}"
        f"，next={format_next_run_display(task)}"
    )


def _schedule_update(raw: str) -> str:
    """解析更新参数，在原任务对象上原子应用并持久化。"""
    from miniagent.assistant.scheduled_tasks.store import (
        format_next_run_display,
        load_tasks,
        repair_invalid_schedules,
        save_tasks,
    )

    parsed = _parse_schedule_mutation(raw)
    if isinstance(parsed, str):
        return parsed.replace("（用于分隔会话参数与 prompt）", "")
    parts, prompt, timezone_override = parsed
    if len(parts) < 4 or parts[0].lower() != "update":
        return "参数不足。\n" + format_schedule_command_usage()
    task_id, kind = parts[1], parts[2].lower()
    tasks = load_tasks()
    existing = next((item for item in tasks if item.id == task_id), None)
    if existing is None:
        return f"未找到任务: {task_id}"
    try:
        schedule, session = _build_schedule(
            command="update",
            task_id=task_id,
            kind=kind,
            tail=parts[3:],
            timezone=_resolve_schedule_tz(timezone_override, existing=existing),
        )
    except ValueError as error:
        message = str(error)
        if message == "参数不足":
            return "参数不足。\n" + format_schedule_command_usage()
        if message.startswith("调度类型"):
            return message + "。\n" + format_schedule_command_usage()
        return f"{ERROR_PREFIX} {message}"
    existing.prompt = prompt
    existing.schedule = schedule
    existing.session = session
    existing.enabled = True
    existing.last_error = None
    validation_error = _validate_next_run(existing, new_task=False)
    repair_invalid_schedules(tasks)
    save_tasks(tasks)
    if validation_error:
        return validation_error
    return (
        f"{SUCCESS_PREFIX} 已更新任务 {task_id}，timezone={existing.schedule.timezone}"
        f"，next={format_next_run_display(existing)}"
    )


def cmd_schedule(text: str, *, allow_mutations: bool) -> str:
    """处理 ``/schedule`` 命令：列出/展示/增删改定时任务；飞书等非变异渠道受 ``allow_mutations`` 限制。"""
    raw = (text or "").strip()
    if not raw.lower().startswith("/schedule"):
        return format_schedule_command_usage()
    rest = raw[9:].strip()  # len("/schedule")
    if not rest:
        return format_schedule_command_usage()
    parts = rest.split()
    sub = parts[0].lower()

    if sub == "list":
        return _schedule_list()

    if sub == "show" and len(parts) >= 2:
        return _schedule_show(parts[1])

    if not allow_mutations:
        if sub in ("add", "update", "remove", "enable", "disable"):
            return f"{WARNING_PREFIX} 当前渠道不允许修改定时任务，请在本地 MiniAgent CLI 执行。"

    if sub == "remove" and len(parts) >= 2:
        return _schedule_remove(parts[1])

    if sub == "enable" and len(parts) >= 2:
        return _schedule_set_enabled(parts[1], enabled=True)

    if sub == "disable" and len(parts) >= 2:
        return _schedule_set_enabled(parts[1], enabled=False)

    if sub == "add":
        return _schedule_add(raw)

    if sub == "update":
        return _schedule_update(raw)

    return format_schedule_command_usage()


__all__ = ["cmd_schedule", "format_schedule_command_usage"]
