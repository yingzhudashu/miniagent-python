"""可选：监视技能目录变更并自动 refresh（``MINIAGENT_SKILLS_WATCH=1``）。

通过定期扫描技能目录下文件的 mtime 检测变更，发现变化后经防抖（2 秒）触发
``refresh_skills`` 重新加载技能。轮询间隔由调用方 ``start_skills_watch`` 的
``interval`` 参数控制（默认 5 秒）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.skills.paths import get_skills_root
from miniagent.skills.refresh import refresh_skills

_logger = get_logger(__name__)

_DEBOUNCE_SEC = 2.0


def skills_watch_enabled() -> bool:
    """是否启用技能目录监视。"""
    return os.environ.get("MINIAGENT_SKILLS_WATCH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _scan_mtimes(root: str) -> dict[str, float]:
    """扫描技能根下文件 mtime 快照。"""
    out: dict[str, float] = {}
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                out[path] = os.path.getmtime(path)
            except OSError:
                continue
    return out


async def _watch_loop(
    registry: Any,
    skill_registry: Any,
    state: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    """轮询技能目录 mtime，变更后 debounce 触发全量 refresh。"""
    root = get_skills_root()
    prev = _scan_mtimes(root)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
            if stop_event.is_set():
                break
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        cur = _scan_mtimes(root)
        if cur != prev:
            prev = cur
            await asyncio.sleep(_DEBOUNCE_SEC)
            if stop_event.is_set():
                break
            cur2 = _scan_mtimes(root)
            if cur2 != cur:
                prev = cur2
                continue
            try:
                sm = state.get("session_manager")
                await refresh_skills(
                    registry,
                    skill_registry,
                    state=state,
                    session_manager=sm,
                )
                _logger.info("MINIAGENT_SKILLS_WATCH: 已自动 refresh 技能")
            except Exception:
                _logger.exception("MINIAGENT_SKILLS_WATCH: refresh 失败")
            prev = cur2


def start_skills_watch(
    registry: Any,
    skill_registry: Any,
    state: dict[str, Any],
    ctx: Any,
) -> asyncio.Task[Any] | None:
    """若 ``MINIAGENT_SKILLS_WATCH`` 开启则启动后台监视任务。"""
    if not skills_watch_enabled():
        return None
    stop_event = asyncio.Event()
    ctx.skills_watch_stop_event = stop_event

    async def _runner() -> None:
        await _watch_loop(registry, skill_registry, state, stop_event)

    task = asyncio.create_task(_runner(), name="miniagent_skills_watch")
    ctx.skills_watch_task = task
    _logger.info("MINIAGENT_SKILLS_WATCH: 已启动技能目录监视")
    return task


__all__ = ["skills_watch_enabled", "start_skills_watch"]
