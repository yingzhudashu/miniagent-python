"""卡片按钮回调幂等去重（进程内 LRU）。"""

from __future__ import annotations

from collections import OrderedDict

_MAX = 256
_seen: OrderedDict[str, float] = OrderedDict()


def should_skip_card_action(dedupe_key: str) -> bool:
    key = (dedupe_key or "").strip()
    if not key:
        return False
    if key in _seen:
        _seen.move_to_end(key)
        return True
    _seen[key] = 0.0
    if len(_seen) > _MAX:
        _seen.popitem(last=False)
    return False


def reset_card_action_dedupe_for_tests() -> None:
    _seen.clear()


__all__ = ["reset_card_action_dedupe_for_tests", "should_skip_card_action"]
