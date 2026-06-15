"""可选：监视技能目录变更并自动 refresh（配置 ``features.skills_watch=true``）。

通过定期扫描所有技能根目录（主根 + 会话技能目录）下文件的 mtime 检测变更，
发现变化后经防抖（2 秒）触发 ``refresh_skills`` 重新加载技能。轮询间隔为 3 秒。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.skills.paths import get_all_skill_roots
from miniagent.skills.refresh import refresh_skills

_logger = get_logger(__name__)

_DEBOUNCE_SEC = 2.0
_POLL_INTERVAL_SEC = 3.0


def skills_watch_enabled() -> bool:
    """是否启用技能目录监视（``features.skills_watch``）。"""
    return get_config("features.skills_watch", False)


def _scan_mtimes(root: str) -> dict[str, float]:
    """扫描单个技能根下文件 mtime 快照。"""
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


def _scan_all_skill_mtimes() -> dict[str, float]:
    """扫描所有技能根（主根 + 会话目录）的 mtime 快照。"""
    merged: dict[str, float] = {}
    for root in get_all_skill_roots(include_sessions=True):
        merged.update(_scan_mtimes(root))
    return merged


async def _watch_loop(
    registry: Any,
    skill_registry: Any,
    state: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    """轮询技能目录 mtime，变更后 debounce 触发全量 refresh。"""
    prev = _scan_all_skill_mtimes()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL_SEC)
            if stop_event.is_set():
                break
        except asyncio.TimeoutError:
            _logger.debug("技能监控等待超时")
        if stop_event.is_set():
            break
        cur = _scan_all_skill_mtimes()
        if cur != prev:
            prev = cur
            await asyncio.sleep(_DEBOUNCE_SEC)
            if stop_event.is_set():
                break
            cur2 = _scan_all_skill_mtimes()
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
                _logger.info("skills_watch: 已自动 refresh 技能")
            except Exception:
                _logger.exception("skills_watch: refresh 失败")
            prev = cur2


def start_skills_watch(
    registry: Any,
    skill_registry: Any,
    state: dict[str, Any],
    ctx: Any,
) -> asyncio.Task[Any] | None:
    """若 ``features.skills_watch`` 为 true 则启动后台监视任务。"""
    if not skills_watch_enabled():
        return None
    stop_event = asyncio.Event()
    ctx.skills_watch_stop_event = stop_event

    async def _runner() -> None:
        await _watch_loop(registry, skill_registry, state, stop_event)

    task = asyncio.create_task(_runner(), name="miniagent_skills_watch")
    ctx.skills_watch_task = task
    _logger.info("skills_watch: 已启动技能目录监视（主根 + 会话技能目录）")
    return task


__all__ = ["skills_watch_enabled", "start_skills_watch"]
