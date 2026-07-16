"""Mini Agent Python — 后台任务命令

CLI 命令实现：启动、查询、取消后台任务。

对应子命令（由 ``command_dispatch`` 路由）：

- ``/btw start <prompt>`` → ``cmd_btw_start``
- ``/btw status [task_id]`` → ``cmd_btw_status``
- ``/btw result <task_id>`` → ``cmd_btw_result``
- ``/btw cancel <task_id>`` → ``cmd_btw_cancel``
- ``/btw clear`` → ``cmd_btw_clear``

管理器由 ``ApplicationContainer.background_tasks`` 显式注入；并行上限由
``BackgroundTaskManager`` 与配置 ``agent.max_parallel_sessions`` 共同决定。
"""

from __future__ import annotations

from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.background_tasks import BackgroundTaskManager
from miniagent.assistant.engine.cli_state import CliLoopState


def _truncate_preview(text: str, max_len: int) -> str:
    """截断预览文本，仅在超长时追加省略号。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


async def cmd_btw_start(
    manager: BackgroundTaskManager,
    engine: Any,
    prompt: str,
    state: CliLoopState | dict[str, Any],
) -> str:
    """启动后台任务。

    Args:
        engine: 需实现 ``run_agent_with_thinking`` 的引擎实例（如 AssistantTurnService）
        prompt: 用户输入
        state: 主 session 状态（含 skill_toolboxes、runtime_ctx 等）

    Returns:
        操作结果消息（成功、并行上限警告或错误）
    """
    try:
        task_id = await manager.start_task(engine, prompt, state)
        preview = _truncate_preview(prompt, 50)
        return (
            f"{SUCCESS_PREFIX} 后台任务已启动: {task_id}\n"
            f"   输入: {preview}\n"
            f"   使用 /btw status {task_id} 查看进度"
        )
    except RuntimeError as e:
        return f"{WARNING_PREFIX} {e}"
    except Exception as e:
        return f"{ERROR_PREFIX} 启动失败: {e}"


def cmd_btw_status(manager: BackgroundTaskManager, task_id: str | None = None) -> str:
    """查看后台任务状态。

    Args:
        task_id: 任务 ID；为 None 时列出全部任务

    Returns:
        Markdown 格式的状态信息
    """
    if task_id:
        status = manager.get_status(task_id)
        if status is None:
            return f"{ERROR_PREFIX} 任务 {task_id} 不存在"

        lines = [
            f"## 任务 {task_id}",
            "",
            f"**状态**: {status['status']}",
            f"**输入**: {_truncate_preview(status['prompt'], 100)}",
            f"**创建时间**: {status['created_at'][:16]}",
        ]

        if status["started_at"]:
            lines.append(f"**开始时间**: {status['started_at'][:16]}")
        if status["completed_at"]:
            lines.append(f"**完成时间**: {status['completed_at'][:16]}")
        if status["has_result"]:
            lines.append("")
            lines.append(f"**✅ 有结果可用**，使用 `/btw result {task_id}` 获取")
        if status["has_error"]:
            lines.append("")
            lines.append(f"**⚠️ 执行出错**，使用 `/btw result {task_id}` 查看错误")

        return "\n".join(lines)

    tasks = manager.list_tasks()

    if not tasks:
        return "📭 当前没有后台任务"

    lines = ["## 后台任务列表", ""]
    for task in tasks:
        status_icon = {
            "pending": "⏳",
            "running": "🔄",
            "completed": SUCCESS_PREFIX,
            "failed": ERROR_PREFIX,
            "cancelled": "🚫",
        }.get(task["status"], "?")

        preview = _truncate_preview(task["prompt"], 40)
        lines.append(f"{status_icon} **{task['task_id']}**: {task['status']} - {preview}")

    lines.append("")
    lines.append(f"统计: {len(tasks)} 个任务")
    stats = manager.get_stats()
    lines.append(f"并行上限: {stats['max_concurrent']}，当前运行: {stats['running_tasks']}")

    return "\n".join(lines)


async def cmd_btw_result(manager: BackgroundTaskManager, task_id: str) -> str:
    """获取后台任务结果或错误信息。

    Args:
        task_id: 任务 ID

    Returns:
        任务结果、错误信息或状态提示
    """
    status = manager.get_status(task_id)
    if status is None:
        return f"{ERROR_PREFIX} 任务 {task_id} 不存在"

    if status["status"] in ("running", "pending"):
        return f"⏳ 任务 {task_id} 仍在执行中，请稍后查询"

    error = await manager.get_error(task_id)
    if error is not None:
        return "\n".join(
            [
                f"## 任务 {task_id} 错误",
                "",
                f"{ERROR_PREFIX} {error}",
            ]
        )

    result = await manager.get_result(task_id)
    if result is None:
        return f"{ERROR_PREFIX} 任务 {task_id} 无结果"

    return "\n".join(
        [
            f"## 任务 {task_id} 结果",
            "",
            result,
        ]
    )


async def cmd_btw_cancel(manager: BackgroundTaskManager, task_id: str) -> str:
    """取消后台任务（中止 asyncio 执行并标记为 cancelled）。

    Args:
        task_id: 任务 ID

    Returns:
        操作结果消息
    """
    success = await manager.cancel_task(task_id)
    if success:
        return f"{SUCCESS_PREFIX} 任务 {task_id} 已取消"

    status = manager.get_status(task_id)
    if status is None:
        return f"{ERROR_PREFIX} 任务 {task_id} 不存在"
    return f"{WARNING_PREFIX} 任务 {task_id} 已完成或已取消，无法取消"


def cmd_btw_clear(manager: BackgroundTaskManager) -> str:
    """清理已完成、失败或已取消的任务。

    Returns:
        清理结果消息
    """
    count = manager.clear_completed()

    if count > 0:
        return f"{SUCCESS_PREFIX} 已清理 {count} 个已完成任务"
    return "📭 没有需要清理的任务"


__all__ = [
    "cmd_btw_start",
    "cmd_btw_status",
    "cmd_btw_result",
    "cmd_btw_cancel",
    "cmd_btw_clear",
]
