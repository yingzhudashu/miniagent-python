"""卡片按钮回调幂等去重（每个飞书运行时独立的有界 LRU）。"""

from __future__ import annotations

from collections import OrderedDict

from miniagent.agent.constants import CARD_DEDUPE_MAX_SIZE

_CARD_DEDUPE_MAX_SIZE = CARD_DEDUPE_MAX_SIZE


class CardActionDeduplicator:
    """Bounded card-action dedupe cache owned by one Feishu runtime."""

    def __init__(self, max_size: int = _CARD_DEDUPE_MAX_SIZE) -> None:
        self._max_size = max_size
        self._seen: OrderedDict[str, None] = OrderedDict()

    def should_skip(self, dedupe_key: str) -> bool:
        """检查卡片操作是否已在当前运行时的缓存中。

        使用有界 LRU，防止飞书卡片按钮被重复点击导致重复执行。

        Args:
            dedupe_key: 去重键（通常为 action_id + chat_id 组合）

        Returns:
            True 表示该操作已处理过，应跳过
        """
        key = (dedupe_key or "").strip()
        if not key:
            return False
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False


__all__ = ["CardActionDeduplicator"]
