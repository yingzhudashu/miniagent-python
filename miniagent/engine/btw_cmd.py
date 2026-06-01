"""Mini Agent Python — 后台任务命令

CLI命令实现：启动、查询、取消后台任务。
"""

from __future__ import annotations

from typing import Any

from miniagent.engine.background_tasks import BackgroundTaskManager

# 全局后台任务管理器（进程级单例）
_bg_manager: BackgroundTaskManager | None = None


def get_background_task_manager() -> BackgroundTaskManager:
    """获取全局后台任务管理器实例"""
    global _bg_manager
    if _bg_manager is None:
        _bg_manager = BackgroundTaskManager(max_concurrent=4)
    return _bg_manager


async def cmd_btw_start(
    engine: Any,
    prompt: str,
    state: dict[str, Any],
) -> str:
    """启动后台任务

    Args:
        engine: UnifiedEngine实例
        prompt: 用户输入
        state: 主session状态

    Returns:
        操作结果消息
    """
    manager = get_background_task_manager()

    try:
        task_id = await manager.start_task(engine, prompt, state)
        return f"✅ 后台任务已启动: {task_id}\n   输入: {prompt[:50]}...\n   使用 /btw status {task_id} 查看进度"
    except RuntimeError as e:
        return f"⚠️ {e}"
    except Exception as e:
        return f"❌ 启动失败: {e}"


def cmd_btw_status(task_id: str | None = None) -> str:
    """查看后台任务状态

    Args:
        task_id: 任务ID（None时显示所有任务）

    Returns:
        状态信息
    """
    manager = get_background_task_manager()

    if task_id:
        status = manager.get_status(task_id)
        if status is None:
            return f"❌ 任务 {task_id} 不存在"

        lines = [
            f"## 任务 {task_id}",
            "",
            f"**状态**: {status['status']}",
            f"**输入**: {status['prompt'][:100]}...",
            f"**创建时间**: {status['created_at'][:16]}",
        ]

        if status['started_at']:
            lines.append(f"**开始时间**: {status['started_at'][:16]}")
        if status['completed_at']:
            lines.append(f"**完成时间**: {status['completed_at'][:16]}")
        if status['has_result']:
            lines.append("")
            lines.append("**✅ 有结果可用**，使用 `/btw result {task_id}` 获取")
        if status['has_error']:
            lines.append("")
            lines.append("**⚠️ 执行出错**，使用 `/btw result {task_id}` 查看错误")

        return "\n".join(lines)

    else:
        # 显示所有任务
        tasks = manager.list_tasks()

        if not tasks:
            return "📭 当前没有后台任务"

        lines = ["## 后台任务列表", ""]
        for task in tasks:
            status_icon = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }.get(task['status'], "?")

            lines.append(
                f"{status_icon} **{task['task_id']}**: {task['status']} - {task['prompt'][:40]}..."
            )

        lines.append("")
        lines.append(f"统计: {len(tasks)} 个任务")
        stats = manager.get_stats()
        lines.append(f"并行上限: {stats['max_concurrent']}，当前运行: {stats['running_tasks']}")

        return "\n".join(lines)


async def cmd_btw_result(task_id: str) -> str:
    """获取后台任务结果

    Args:
        task_id: 任务ID

    Returns:
        任务结果或错误信息
    """
    manager = get_background_task_manager()

    result = await manager.get_result(task_id)
    if result is None:
        status = manager.get_status(task_id)
        if status is None:
            return f"❌ 任务 {task_id} 不存在"
        elif status['status'] == 'running':
            return f"⏳ 任务 {task_id} 仍在执行中，请稍后查询"
        else:
            return f"❌ 任务 {task_id} 无结果"

    lines = [
        f"## 任务 {task_id} 结果",
        "",
        result,
    ]
    return "\n".join(lines)


async def cmd_btw_cancel(task_id: str) -> str:
    """取消后台任务

    Args:
        task_id: 任务ID

    Returns:
        操作结果消息
    """
    manager = get_background_task_manager()

    success = await manager.cancel_task(task_id)
    if success:
        return f"✅ 任务 {task_id} 已取消"
    else:
        status = manager.get_status(task_id)
        if status is None:
            return f"❌ 任务 {task_id} 不存在"
        else:
            return f"⚠️ 任务 {task_id} 已完成或已取消，无法取消"


def cmd_btw_clear() -> str:
    """清理已完成的任务

    Returns:
        清理结果消息
    """
    manager = get_background_task_manager()
    count = manager.clear_completed()

    if count > 0:
        return f"✅ 已清理 {count} 个已完成任务"
    else:
        return "📭 没有需要清理的任务"


__all__ = [
    "get_background_task_manager",
    "cmd_btw_start",
    "cmd_btw_status",
    "cmd_btw_result",
    "cmd_btw_cancel",
    "cmd_btw_clear",
]