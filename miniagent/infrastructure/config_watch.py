"""配置文件热更新监听器。

监听 config.user.json 的修改并自动触发 reload_config()，
无需重启即可生效配置更改。

配置项：
- features.config_hot_reload: true 开启热更新（默认 false）

使用方式：
- 在 main.py 启动时调用 start_config_watch(ctx)
- 修改 config.user.json 后会自动触发 reload
- 或手动调用 /reload-config 命令
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from miniagent.infrastructure.json_config import get_config, reload_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# 防抖间隔（秒）
_DEBOUNCE_SEC = 2.0
# 检查间隔（秒）
_CHECK_INTERVAL = 5.0


async def _config_watch_loop(stop_event: asyncio.Event) -> None:
    """监听 config.user.json 的修改并触发热更新。

    Args:
        stop_event: 停止信号（进程退出时设置）

    流程：
    1. 每 5 秒检查配置文件的 mtime
    2. 检测到修改后等待 2 秒（防抖）
    3. 调用 reload_config() 重新加载配置
    """
    # 配置文件路径（项目根目录下的 config.user.json）
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.user.json"

    prev_mtime: float = 0.0

    # 初始 mtime
    if config_path.exists():
        try:
            prev_mtime = config_path.stat().st_mtime
        except Exception as e:
            _logger.debug("获取配置文件mtime失败: %s", e)

    while not stop_event.is_set():
        # 等待检查间隔或停止信号
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_CHECK_INTERVAL)
            if stop_event.is_set():
                break
        except asyncio.TimeoutError:
            _logger.debug("配置监控等待超时，继续检查")

        if stop_event.is_set():
            break

        # 检查配置文件是否存在及 mtime
        try:
            if config_path.exists():
                cur_mtime = config_path.stat().st_mtime

                # 检测到修改
                if cur_mtime != prev_mtime:
                    prev_mtime = cur_mtime

                    # 防抖：等待文件写入完成
                    await asyncio.sleep(_DEBOUNCE_SEC)

                    if stop_event.is_set():
                        break

                    # 再次检查 mtime（可能仍在写入）
                    try:
                        cur_mtime2 = config_path.stat().st_mtime
                        if cur_mtime2 != cur_mtime:
                            prev_mtime = cur_mtime2
                            continue  # 继续等待
                    except Exception:
                        continue

                    # 触发热更新
                    try:
                        reload_config()
                        _logger.info("配置已热更新（检测到 config.user.json 修改）")
                    except Exception as e:
                        _logger.error(f"配置热更新失败: {e}")

        except Exception as e:
            _logger.debug(f"配置文件检查失败: {e}")


def start_config_watch(ctx: Any) -> asyncio.Task | None:
    """启动配置文件监听。

    Args:
        ctx: RuntimeContext 实例

    Returns:
        监听任务（或 None 如果未启用）

    Note:
        需配置 features.config_hot_reload=true 才会启动
    """
    # 检查是否启用热更新
    if not get_config("features.config_hot_reload", False):
        _logger.debug("配置热更新未启用（设置 features.config_hot_reload=true）")
        return None

    # 创建停止信号
    stop_event = asyncio.Event()
    ctx.config_watch_stop_event = stop_event

    # 启动监听任务
    async def _runner():
        await _config_watch_loop(stop_event)

    task = asyncio.create_task(_runner(), name="miniagent_config_watch")
    ctx.config_watch_task = task

    _logger.info("配置热更新已启用（监听 config.user.json）")
    return task


def stop_config_watch(ctx: Any) -> None:
    """停止配置文件监听（进程退出时调用）。

    Args:
        ctx: RuntimeContext 实例
    """
    stop_event = getattr(ctx, "config_watch_stop_event", None)
    if stop_event is not None:
        stop_event.set()

    task = getattr(ctx, "config_watch_task", None)
    if task is not None and not task.done():
        task.cancel()
        # 不等待任务完成（避免阻塞关闭流程）


__all__ = ["start_config_watch", "stop_config_watch"]