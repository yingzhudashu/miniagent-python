"""飞书消息去重模块

内存+磁盘双重去重，防止同一消息被重复处理。

核心机制：
- 内存去重：处理中的消息 claim（_processing_claims）
- 磁盘去重：已处理消息的持久化记录（_disk_dedup）
- 惰性清理：仅在超过阈值时清理过期条目
- 延迟刷盘：使用脏标记，定期或超阈值时异步刷盘

配置项：
- DEDUP_TTL_MS: 去重条目有效期（默认 5 分钟）
- DEDUP_MAX_SIZE: 最大去重条目数（默认 2000）
- DEDUP_FLUSH_INTERVAL: 刷盘间隔（默认 60 秒）
- DEDUP_FLUSH_THRESHOLD: 触发刷盘的条目阈值（默认 1000）

使用方式：
    # 尝试获取处理权
    if try_begin_processing(message_id):
        # 处理消息
        ...
        # 完成后释放并记录
        release_processing(message_id)

性能优化：
- 内存缓存所有去重数据，避免每次消息都读取磁盘
- 使用脏标记延迟刷盘，减少 I/O 次数
- 惰性清理过期条目，避免每次都遍历字典
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# ─── 常量配置 ───

DEDUP_TTL_MS = 5 * 60 * 1000  # 5 分钟有效期
DEDUP_MAX_SIZE = 2000  # 最大条目数

# 性能优化：刷盘配置（支持环境变量覆盖）
import os as _os_for_dedup
DEDUP_FLUSH_INTERVAL = int(_os_for_dedup.environ.get("MINIAGENT_DEDUP_FLUSH_INTERVAL", "30"))  # 降低到30秒
DEDUP_FLUSH_THRESHOLD = int(_os_for_dedup.environ.get("MINIAGENT_DEDUP_FLUSH_THRESHOLD", "500"))  # 降低到500条

# ─── 状态目录 ───

_state_dir = os.path.join(
    get_config("paths.state_dir", os.path.join(os.getcwd(), "workspaces")),
    "feishu",
    "dedup",
)
_dedup_file = os.path.join(_state_dir, "processed.json")

# ─── 内存状态 ───

_processing_claims: dict[str, float] = {}
_disk_dedup: dict[str, float] = {}
_disk_dedup_dirty: bool = False  # 性能优化：脏标记
_last_flush_time: float = 0  # 上次刷盘时间


# ─── 内部函数 ───


def _ensure_state_dir() -> None:
    """确保状态目录存在。"""
    os.makedirs(_state_dir, exist_ok=True)


def _resolve_dedup_key(message_id: str) -> str:
    """解析去重键。"""
    return f"mini-agent:{message_id.strip()}"


def _load_disk_dedup() -> None:
    """加载磁盘去重数据（启动时一次性加载）。

    性能优化：仅在模块初始化时同步读取，运行时所有去重操作都在内存中完成。
    """
    global _disk_dedup
    try:
        _ensure_state_dir()
        if os.path.isfile(_dedup_file):
            with open(_dedup_file, encoding="utf-8") as f:
                _disk_dedup = json.load(f)
    except Exception:
        _disk_dedup = {}


def _save_disk_dedup() -> None:
    """保存磁盘去重数据（同步版本，仅在必要时调用）。

    性能优化：使用脏标记延迟刷盘，避免每次消息都写磁盘。
    """
    global _disk_dedup_dirty
    if not _disk_dedup_dirty:
        return
    try:
        _ensure_state_dir()
        with open(_dedup_file, "w", encoding="utf-8") as f:
            json.dump(_disk_dedup, f)  # 紧凑格式
        _disk_dedup_dirty = False
    except Exception as e:
        _logger.debug("同步保存去重数据失败: %s", e)


async def _save_disk_dedup_async() -> None:
    """异步保存磁盘去重数据（不阻塞事件循环）。

    性能优化：使用 asyncio.to_thread 包装文件写入。
    """
    global _disk_dedup_dirty
    if not _disk_dedup_dirty:
        return

    def _sync_save() -> None:
        try:
            _ensure_state_dir()
            with open(_dedup_file, "w", encoding="utf-8") as f:
                json.dump(_disk_dedup, f)
        except Exception as e:
            _logger.debug("保存去重数据失败: %s", e)

    try:
        await asyncio.to_thread(_sync_save)
        _disk_dedup_dirty = False
    except Exception as e:
        _logger.debug("同步保存去重数据失败: %s", e)


def _maybe_trigger_flush() -> None:
    """检查是否需要触发异步刷盘。

    性能优化：仅在超过阈值或时间间隔时刷盘，减少 I/O 次数。
    """
    global _last_flush_time
    now = time.monotonic()

    need_flush = (
        len(_disk_dedup) >= DEDUP_FLUSH_THRESHOLD
        or now - _last_flush_time >= DEDUP_FLUSH_INTERVAL
    )

    if need_flush and _disk_dedup_dirty:
        _last_flush_time = now
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_save_disk_dedup_async())
        except Exception as e:
            _logger.debug("触发异步刷盘失败: %s", e)


def _prune_claims_if_needed() -> None:
    """惰性清理过期去重条目。

    仅当超过 80% 阈值时才执行清理，避免每次消息处理都遍历整个字典。
    """
    global _disk_dedup_dirty
    threshold = int(DEDUP_MAX_SIZE * 0.8)
    if len(_processing_claims) <= threshold and len(_disk_dedup) <= threshold:
        return

    cutoff = time.time() - DEDUP_TTL_MS / 1000.0

    if len(_processing_claims) > threshold:
        to_remove = [k for k, v in _processing_claims.items() if v < cutoff]
        for k in to_remove:
            del _processing_claims[k]

    if len(_disk_dedup) > threshold:
        to_remove = [k for k, v in _disk_dedup.items() if v < cutoff]
        for k in to_remove:
            del _disk_dedup[k]
        _disk_dedup_dirty = True

    _maybe_trigger_flush()


def _flush_dedup_at_exit() -> None:
    """进程退出时同步刷盘（atexit 回调）。"""
    global _disk_dedup_dirty
    if _disk_dedup_dirty:
        _save_disk_dedup()


# ─── 公共函数 ───


def try_begin_processing(message_id: str) -> bool:
    """尝试获取消息处理权。

    Args:
        message_id: 飞书消息 ID

    Returns:
        True = 首次处理，可以处理；False = 重复/处理中，跳过
    """
    key = _resolve_dedup_key(message_id)
    if not key:
        return True

    now = time.time()
    _prune_claims_if_needed()

    if key in _disk_dedup:
        return False

    if key in _processing_claims:
        return False

    _processing_claims[key] = now
    return True


def release_processing(message_id: str) -> None:
    """释放处理权并记录到磁盘去重。

    Args:
        message_id: 飞书消息 ID
    """
    global _disk_dedup_dirty
    key = _resolve_dedup_key(message_id)
    if not key:
        return

    _processing_claims.pop(key, None)
    _disk_dedup[key] = time.time()
    _disk_dedup_dirty = True

    if len(_disk_dedup) > DEDUP_MAX_SIZE:
        sorted_items = sorted(_disk_dedup.items(), key=lambda x: x[1])
        to_remove = len(sorted_items) // 5
        for k, _ in sorted_items[:to_remove]:
            del _disk_dedup[k]

    _maybe_trigger_flush()


def abandon_processing_claim(message_id: str) -> None:
    """仅丢弃内存中的处理权，不写入磁盘去重。

    用于可恢复失败时调用，避免永久跳过该消息。

    Args:
        message_id: 飞书消息 ID
    """
    key = _resolve_dedup_key(message_id)
    if not key:
        return
    _processing_claims.pop(key, None)


def get_dedup_stats() -> dict[str, Any]:
    """获取去重统计信息（用于调试）。"""
    return {
        "processing_claims": len(_processing_claims),
        "disk_dedup": len(_disk_dedup),
        "dirty": _disk_dedup_dirty,
        "state_dir": _state_dir,
    }


# ─── 初始化 ───

import atexit

atexit.register(_flush_dedup_at_exit)
_load_disk_dedup()


__all__ = [
    "try_begin_processing",
    "release_processing",
    "abandon_processing_claim",
    "get_dedup_stats",
    "DEDUP_TTL_MS",
    "DEDUP_MAX_SIZE",
    "DEDUP_FLUSH_INTERVAL",
    "DEDUP_FLUSH_THRESHOLD",
]