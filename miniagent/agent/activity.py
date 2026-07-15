"""Small activity-log invocation adapter for sync and async implementations."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any


async def invoke_activity_log(
    activity_log: Any,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    async_method = getattr(activity_log, f"{method_name}_async", None)
    if callable(async_method) and inspect.iscoroutinefunction(async_method):
        await async_method(*args, **kwargs)
        return
    sync_method = getattr(activity_log, method_name, None)
    if callable(sync_method):
        await asyncio.to_thread(sync_method, *args, **kwargs)


__all__ = ["invoke_activity_log"]
