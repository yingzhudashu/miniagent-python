"""卡片按钮回调幂等去重（进程内 LRU）。

配置项可通过环境变量 MINIAGENT_CARD_DEDUPE_MAX_SIZE 覆盖。
"""

from __future__ import annotations

import os
from collections import OrderedDict

# 直接读取环境变量，避免触发循环导入
_CARD_DEDUPE_MAX_SIZE = int(os.environ.get("MINIAGENT_CARD_DEDUPE_MAX_SIZE", "256"))

_seen: OrderedDict[str, float] = OrderedDict()


def should_skip_card_action(dedupe_key: str) -> bool:
    """检查卡片操作是否已在进程内去重缓存中。

    使用 LRU 缓存（最大 CARD_DEDUPE_MAX_SIZE 条），防止飞书卡片按钮被重复点击导致重复执行。

    Args:
        dedupe_key: 去重键（通常为 action_id + chat_id 组合）

    Returns:
        True 表示该操作已处理过，应跳过
    """
    key = (dedupe_key or "").strip()
    if not key:
        return False
    if key in _seen:
        _seen.move_to_end(key)
        return True
    _seen[key] = 0.0
    if len(_seen) > _CARD_DEDUPE_MAX_SIZE:
        _seen.popitem(last=False)
    return False


def reset_card_action_dedupe_for_tests() -> None:
    _seen.clear()


__all__ = ["reset_card_action_dedupe_for_tests", "should_skip_card_action"]
