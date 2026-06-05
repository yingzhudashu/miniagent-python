"""性能优化基础设施模块

提供：
- 正则表达式预编译缓存
- JSON序列化/解析缓存
- 字符串操作优化工具
- 内存使用监控

使用原则：
- 高频调用路径必须使用缓存
- 低频调用可选使用
- 所有缓存都有上限保护（防止内存泄漏）
"""

from __future__ import annotations

import functools
import json
import re
from collections import OrderedDict
from threading import Lock
from typing import Any, Callable


# ─── 正则表达式预编译缓存 ────────────────────────────────────────

_REGEX_CACHE_MAX_SIZE = 200
_regex_cache: OrderedDict[str, re.Pattern] = OrderedDict()
_regex_cache_lock = Lock()


def get_compiled_pattern(pattern: str, flags: int = 0) -> re.Pattern:
    """获取预编译的正则表达式（带缓存）。

    Args:
        pattern: 正则表达式字符串
        flags: 正则标志（如 re.IGNORECASE）

    Returns:
        预编译的 re.Pattern 对象

    Example:
        >>> pat = get_compiled_pattern(r"@file:(\\S+)")  # 使用原始字符串
        >>> matches = pat.findall(text)
    """
    cache_key = f"{pattern}:{flags}"

    with _regex_cache_lock:
        if cache_key in _regex_cache:
            # 移动到末尾（标记为最近使用）
            _regex_cache.move_to_end(cache_key)
            return _regex_cache[cache_key]

        # 编译新正则
        compiled = re.compile(pattern, flags)
        _regex_cache[cache_key] = compiled

        # LRU 驎出
        while len(_regex_cache) > _REGEX_CACHE_MAX_SIZE:
            _regex_cache.popitem(last=False)

        return compiled


# ─── JSON 序列化/解析缓存 ──────────────────────────────────────────

# 性能优化：提高上限并配置化（从100提高到500）
_JSON_SERIALIZE_CACHE_MAX_SIZE = 500  # 配置化上限
_json_serialize_cache: OrderedDict[str, str] = OrderedDict()
_json_serialize_lock = Lock()


def cached_json_serialize(obj: Any, max_len: int | None = None) -> str:
    """智能 JSON 序列化缓存（性能优化增强版）。

    性能优化：
    - 提高上限并配置化（从100提高到500）
    - 大对象（>1000字符）直接序列化不缓存
    - 小对象优先缓存（高频重复对象）
    - 配置项：perf.json_cache_max_size

    Args:
        obj: 要序列化的对象
        max_len: 最大长度（可选，超过会截断）

    Returns:
        JSON 字符串

    Note:
        仅用于高频重复对象（如工具 schema），不适合动态数据。
    """
    # 性能优化：预判对象大小，大对象不缓存
    # 对不可哈希对象，先估算大小
    if not isinstance(obj, (str, int, float, bool, tuple, frozenset)):
        try:
            # 大对象（>1000字符）：直接序列化，不缓存
            obj_str = str(obj)
            if len(obj_str) > 1000:
                result = json.dumps(obj, ensure_ascii=False)
                if max_len and len(result) > max_len:
                    return result[:max_len] + "...[截断]"
                return result

            # 小对象：直接序列化（不缓存不可哈希对象）
            result = json.dumps(obj, ensure_ascii=False)
            if max_len and len(result) > max_len:
                return result[:max_len] + "...[截断]"
            return result
        except Exception:
            return str(obj)[:max_len or 1000]

    # 可哈希对象：判断大小
    try:
        obj_str = str(obj)
        # 大对象（>1000字符）：直接序列化，不缓存
        if len(obj_str) > 1000:
            result = json.dumps(obj, ensure_ascii=False)
            if max_len and len(result) > max_len:
                return result[:max_len] + "...[截断]"
            return result
    except Exception:
        pass

    # 小对象：使用缓存
    cache_key = f"{obj}:{max_len}"

    with _json_serialize_lock:
        if cache_key in _json_serialize_cache:
            _json_serialize_cache.move_to_end(cache_key)
            return _json_serialize_cache[cache_key]

        try:
            result = json.dumps(obj, ensure_ascii=False)
            if max_len and len(result) > max_len:
                result = result[:max_len] + "...[截断]"
        except Exception:
            result = str(obj)[:max_len or 1000]

        # 性能优化：小对象才缓存
        if len(result) <= 1000:
            _json_serialize_cache[cache_key] = result

            # 动态调整缓存上限（配置化）
            # 优先从配置读取，否则使用默认值500
            try:
                from miniagent.infrastructure.json_config import get_config
                cache_max_size = get_config("perf.json_cache_max_size", 500)
            except Exception:
                cache_max_size = 500

            while len(_json_serialize_cache) > cache_max_size:
                _json_serialize_cache.popitem(last=False)

        return result


# ─── 字符串操作优化 ───────────────────────────────────────────────────


class OptimizedStringBuilder:
    """优化的字符串构建器（减少临时对象）。

    相比直接使用 += 或 join()，在大量拼接时内存效率更高。

    性能优化（增强版）：
    - 动态合并策略：根据chunk大小智能调整
    - 总长度超过10KB时提前合并（避免内存峰值）
    - 单个chunk超大（>5KB）时立即合并
    - 默认阈值降低到30（更激进的合并）

    Example:
        >>> builder = OptimizedStringBuilder()
        >>> for chunk in chunks:
        >>>     builder.append(chunk)
        >>> result = builder.build()
    """

    __slots__ = ("_chunks", "_total_len", "_consolidated", "_merge_threshold")

    def __init__(self, initial_chunks: list[str] | None = None, merge_threshold: int = 30):
        """初始化字符串构建器。

        Args:
            initial_chunks: 初始chunk列表
            merge_threshold: chunk数量阈值（默认30，比之前的50更激进）
        """
        self._chunks: list[str] = initial_chunks or []
        self._total_len: int = sum(len(c) for c in self._chunks)
        self._consolidated: str | None = None
        self._merge_threshold = merge_threshold

    def append(self, chunk: str) -> None:
        """智能追加字符串块（动态合并策略）。

        性能优化：
        - chunk数量超过阈值时合并
        - 总长度超过10KB时提前合并
        - 单个chunk超大（>5KB）时立即合并

        Args:
            chunk: 要追加的字符串块
        """
        if not chunk:
            return

        chunk_len = len(chunk)
        self._chunks.append(chunk)
        self._total_len += chunk_len

        # 动态合并策略
        should_merge = (
            len(self._chunks) > self._merge_threshold or  # chunk数量超过阈值
            self._total_len > 10240 or  # 总长度超过10KB（提前合并）
            chunk_len > 5000  # 单个chunk超大（>5KB）立即合并
        )

        if should_merge:
            self._consolidated = "".join(self._chunks)
            self._chunks = [self._consolidated]
            self._total_len = len(self._consolidated)

    def build(self) -> str:
        """构建最终字符串."""
        if self._consolidated:
            # 已合并过，直接返回
            if len(self._chunks) == 1:
                return self._consolidated
            # 合并后又有新追加
            return self._consolidated + "".join(self._chunks[1:])
        # 未合并过
        return "".join(self._chunks)

    def __len__(self) -> int:
        """返回当前总长度."""
        return self._total_len


# ─── 函数结果缓存装饰器 ───────────────────────────────────────────────


def lru_cache_with_ttl(maxsize: int = 128, ttl_seconds: float = 60.0) -> Callable:
    """带 TTL 的 LRU 缓存装饰器。

    Args:
        maxsize: 最大缓存条数
        ttl_seconds: 缓存过期时间（秒）

    Returns:
        装饰器函数

    Example:
        >>> @lru_cache_with_ttl(maxsize=100, ttl_seconds=5.0)
        >>> def get_terminal_width() -> int:
        >>>     return shutil.get_terminal_size().columns
    """
    import time

    def decorator(func: Callable) -> Callable:
        cache: OrderedDict[Any, tuple[Any, float]] = OrderedDict()
        lock = Lock()

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # 创建缓存键（简化版本，不支持所有参数类型）
            try:
                cache_key = args + tuple(sorted(kwargs.items()))
            except TypeError:
                # 不可哈希参数，直接调用
                return func(*args, **kwargs)

            current_time = time.time()

            with lock:
                # 检查缓存
                if cache_key in cache:
                    result, timestamp = cache[cache_key]
                    # 检查 TTL
                    if current_time - timestamp < ttl_seconds:
                        cache.move_to_end(cache_key)
                        return result
                    # 过期，删除
                    cache.pop(cache_key)

                # 调用函数
                result = func(*args, **kwargs)
                cache[cache_key] = (result, current_time)

                # LRU 鎎出
                while len(cache) > maxsize:
                    cache.popitem(last=False)

                return result

        return wrapper

    return decorator


# ─── 内存使用监控 ───────────────────────────────────────────────────────


def get_memory_usage_mb() -> float:
    """获取当前进程内存使用（MB）."""
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / 1024 / 1024
    except ImportError:
        # psutil 未安装，返回0
        return 0.0


def clear_all_caches() -> None:
    """清空所有缓存（用于测试或内存紧急情况）."""
    with _regex_cache_lock:
        _regex_cache.clear()

    with _json_serialize_lock:
        _json_serialize_cache.clear()


__all__ = [
    "get_compiled_pattern",
    "cached_json_serialize",
    "OptimizedStringBuilder",
    "lru_cache_with_ttl",
    "get_memory_usage_mb",
    "clear_all_caches",
]