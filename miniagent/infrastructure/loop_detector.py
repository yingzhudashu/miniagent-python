"""Mini Agent Python — 循环检测器

防止 Agent 陷入无限循环的机制。

检测器类型：
1. generic_repeat — 检测相同工具 + 相同参数的重复调用
2. known_poll_no_progress — 检测已知轮询模式但无状态变化
3. ping_pong — 检测交替的 A→B→A→B 模式

行为：
- warning_threshold 以下：正常执行
- warning_threshold ~ critical_threshold：发出警告但不拦截
- critical_threshold 以上：强制终止循环

设计原则：
- 渐进式：先警告、后拦截，给 Agent 自我修正的机会
- 低误报：只有完全相同的调用才计数，避免阻断合法重试
- 可配置：所有阈值都可动态调整
"""

from __future__ import annotations

import json
import os as _os_for_loop
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from miniagent.types.agent import LoopDetectionConfig, LoopDetectionResult, LoopLevel

# ─── JSON 序列化缓存（性能优化：避免重复序列化相同参数）──

_args_json_cache: OrderedDict[tuple, str] = OrderedDict()
_ARGS_CACHE_MAX_SIZE = int(_os_for_loop.environ.get("MINIAGENT_ARGS_CACHE_MAX_SIZE", "100"))


def _make_args_cache_key(args: dict[str, Any]) -> tuple:
    """为参数字典生成确定性缓存键（性能优化）。

    使用 tuple 替代 repr 作为缓存键，避免字符串序列化开销。
    对于常见参数类型（str, int, float, bool, None），直接使用值；
    对于复杂类型（dict, list），使用其内容的 tuple 表示。

    Args:
        args: 工具参数字典

    Returns:
        可哈希的 tuple 缓存键
    """
    items: list[tuple[str, Any]] = []
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            items.append((k, v))
        elif isinstance(v, dict):
            # 递归处理嵌套字典
            items.append((k, _make_args_cache_key(v)))
        elif isinstance(v, (list, tuple)):
            # 列表/元组转为 tuple（元素需可哈希）
            try:
                items.append((k, tuple(
                    _make_args_cache_key(i) if isinstance(i, dict) else i
                    if isinstance(i, (str, int, float, bool, type(None)))
                    else repr(i)
                    for i in v
                )))
            except TypeError:
                items.append((k, repr(v)))
        else:
            items.append((k, repr(v)))
    # 按键排序确保确定性（字典顺序不确定）
    return tuple(sorted(items))


def _serialize_args(args: dict[str, Any]) -> str:
    """序列化参数为 JSON，使用缓存避免重复计算。

    性能优化：使用 tuple 缓存键替代 repr，减少序列化开销。
    """
    cache_key = _make_args_cache_key(args)
    if cache_key in _args_json_cache:
        # LRU：移动到末尾
        _args_json_cache.move_to_end(cache_key)
        return _args_json_cache[cache_key]
    # 未缓存，执行序列化
    result = json.dumps(args, ensure_ascii=False)
    _args_json_cache[cache_key] = result
    # 保持缓存大小限制
    if len(_args_json_cache) > _ARGS_CACHE_MAX_SIZE:
        _args_json_cache.popitem(last=False)  # 移除最旧的
    return result


def clear_args_cache() -> None:
    """清除参数序列化缓存（测试用）。"""
    _args_json_cache.clear()


@dataclass
class _CallRecord:
    """工具调用记录"""

    tool: str
    args: str  # JSON 序列化的参数（用于精确匹配）
    result: str  # 结果摘要（用于检测无进展轮询）
    timestamp: float


# 默认配置
_DEFAULT_CONFIG = LoopDetectionConfig(
    enabled=True,
    history_size=30,
    warning_threshold=5,
    critical_threshold=8,
    detectors={
        "generic_repeat": True,
        "known_poll_no_progress": True,
        "ping_pong": True,
    },
)


class LoopDetector:
    """循环检测器

    记录工具调用历史，在每次新调用前检测是否存在循环模式。

    Example:
        detector = LoopDetector()
        detector.record("read_file", {"path": "a.txt"}, "success")
        # ... 重复多次后
        result = detector.check("read_file", {"path": "a.txt"})
        # result.level == "warning" or "critical"
    """

    def __init__(self, config: LoopDetectionConfig | None = None) -> None:
        """创建循环检测器

        Args:
            config: 检测配置，使用默认配置如果为 None
        """
        if config:
            merged = {
                "enabled": config.enabled,
                "history_size": config.history_size,
                "warning_threshold": config.warning_threshold,
                "critical_threshold": config.critical_threshold,
                "detectors": dict(config.detectors) if config.detectors else {},
            }
            self._config = LoopDetectionConfig(**merged)
        else:
            self._config = _DEFAULT_CONFIG
        self._history: list[_CallRecord] = []

    def update_config(self, config: LoopDetectionConfig) -> None:
        """更新配置

        Args:
            config: 新的检测配置
        """
        self._config = config

    def record(self, tool: str, args: dict[str, Any], result: str) -> None:
        """记录一次工具调用

        Args:
            tool: 工具名称
            args: 工具参数
            result: 工具结果
        """
        if not self._config.enabled:
            return

        self._history.append(
            _CallRecord(
                tool=tool,
                args=_serialize_args(args),
                result=result[:200],  # 只保留前 200 字符
                timestamp=time.time(),
            )
        )

        # 保持历史记录在限制范围内
        max_size = self._config.history_size
        if len(self._history) > max_size:
            self._history = self._history[-max_size:]

    def check(self, tool: str, args: dict[str, Any]) -> LoopDetectionResult:
        """检查当前工具调用是否存在循环模式

        检测顺序：
        1. generic_repeat — 相同工具 + 相同参数
        2. known_poll_no_progress — 轮询模式但结果无变化
        3. ping_pong — 交替模式（A→B→A→B）

        Args:
            tool: 即将调用的工具名称
            args: 即将使用的参数

        Returns:
            检测结果
        """
        if not self._config.enabled:
            return LoopDetectionResult(level="none", message="")

        args_str = _serialize_args(args)

        # 检测 1: generic_repeat（相同工具 + 相同参数）
        if self._config.detectors.get("generic_repeat", False):
            repeat_count = sum(1 for r in self._history if r.tool == tool and r.args == args_str)
            if repeat_count >= self._config.critical_threshold:
                return LoopDetectionResult(
                    level="critical",
                    message=(
                        f"检测到循环：{tool} 已重复调用 {repeat_count} 次"
                        f"（参数相同）。强制终止以避免无限循环。"
                    ),
                    pattern=f"{tool}({args_str}) x{repeat_count}",
                )
            if repeat_count >= self._config.warning_threshold:
                return LoopDetectionResult(
                    level="warning",
                    message=(
                        f"警告：{tool} 已重复调用 {repeat_count} 次（参数相同），请考虑改变策略。"
                    ),
                    pattern=f"{tool}({args_str}) x{repeat_count}",
                )

        # 检测 2: known_poll_no_progress（轮询模式但结果无变化）
        if self._config.detectors.get("known_poll_no_progress", False):
            poll_result = self._detect_poll_pattern(tool, args_str)
            if poll_result:
                return poll_result

        # 检测 3: ping_pong（交替模式）
        if self._config.detectors.get("ping_pong", False):
            ping_pong_result = self._detect_ping_pong(tool, args_str)
            if ping_pong_result:
                return ping_pong_result

        return LoopDetectionResult(level="none", message="")

    def _detect_poll_pattern(self, tool: str, args_str: str) -> LoopDetectionResult | None:
        """检测轮询模式：连续调用相同工具但结果无变化"""
        consecutive: list[_CallRecord] = []
        for r in reversed(self._history):
            if r.tool == tool and r.args == args_str:
                consecutive.insert(0, r)
            elif consecutive:
                break

        if len(consecutive) < 3:
            return None

        # 检查结果是否有变化（至少 3 次相同结果）
        results = [r.result for r in consecutive]
        unique_results = set(results)

        if len(unique_results) == 1 and len(consecutive) >= self._config.warning_threshold:
            level: LoopLevel = (
                "critical" if len(consecutive) >= self._config.critical_threshold else "warning"
            )
            return LoopDetectionResult(
                level=level,
                message=(f"检测到无进展轮询：{tool} 连续 {len(consecutive)} 次，结果无变化。"),
                pattern=f"{tool} 轮询 x{len(consecutive)}",
            )

        return None

    def _detect_ping_pong(self, tool: str, args_str: str) -> LoopDetectionResult | None:
        """检测 ping-pong 模式：A→B→A→B→A→B"""
        if len(self._history) < 6:
            return None

        recent = self._history[-6:]
        pattern = [f"{r.tool}:{r.args}" for r in recent]

        a = pattern[0]
        b = pattern[1]
        if a == b:
            return None  # 不是交替

        expected = [a, b, a, b, a, b]
        if pattern == expected:
            return LoopDetectionResult(
                level="warning",
                message=f"检测到 ping-pong 模式：{a} ↔ {b} 交替调用。",
                pattern=f"{a} ↔ {b}",
            )

        return None

    def get_stats(self) -> dict[str, Any]:
        """获取当前统计信息

        Returns:
            包含 total_calls, history_size, enabled 的字典
        """
        return {
            "total_calls": len(self._history),
            "history_size": self._config.history_size,
            "enabled": self._config.enabled,
        }

    def clear(self) -> None:
        """清空历史记录"""
        self._history.clear()


__all__ = ["LoopDetector"]
