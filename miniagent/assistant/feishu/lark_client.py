"""飞书 ``lark-oapi`` 客户端工厂与配置读取。"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from typing import Any

from miniagent.agent.constants import FEISHU_SDK_CLIENT_CACHE_MAX_SIZE
from miniagent.assistant.feishu.types import FeishuConfig

# ─── 客户端缓存（性能优化：避免每次 API 调用重建连接）──

_client_cache: OrderedDict[tuple[str, bytes], Any] = OrderedDict()
_client_cache_lock = threading.Lock()


def _secret_fingerprint(secret: str) -> bytes:
    """Return a non-reversible in-memory cache discriminator."""
    return hashlib.sha256(secret.encode("utf-8")).digest()[:12]


def _create_client(config: FeishuConfig) -> Any:
    """Build one SDK client; split out for deterministic concurrency tests."""
    import lark_oapi as lark

    return lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()


def build_client(config: FeishuConfig) -> Any:
    """获取或复用 Lark SDK 客户端；密钥轮换时原子替换旧实例。"""
    key = (config.app_id, _secret_fingerprint(config.app_secret))
    with _client_cache_lock:
        cached = _client_cache.get(key)
        if cached is not None:
            _client_cache.move_to_end(key)
            return cached

        client = _create_client(config)
        stale_keys = [cached_key for cached_key in _client_cache if cached_key[0] == config.app_id]
        for stale_key in stale_keys:
            _client_cache.pop(stale_key, None)
        _client_cache[key] = client
        while len(_client_cache) > FEISHU_SDK_CLIENT_CACHE_MAX_SIZE:
            _client_cache.popitem(last=False)
        return client


def clear_client_cache() -> None:
    """清除客户端缓存（测试用）。"""
    with _client_cache_lock:
        _client_cache.clear()


def config_from_env() -> FeishuConfig | None:
    """从环境变量读取飞书配置。"""
    aid = (os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if not aid or not sec:
        return None
    return FeishuConfig(
        app_id=aid,
        app_secret=sec,
        encrypt_key=(os.environ.get("FEISHU_ENCRYPT_KEY") or "").strip() or None,
        verification_token=(os.environ.get("FEISHU_VERIFICATION_TOKEN") or "").strip() or None,
    )


def require_lark_oapi() -> None:
    """确认 ``lark-oapi`` 已安装；未安装时抛出 ``ImportError``。"""
    import lark_oapi  # noqa: F401


__all__ = ["build_client", "clear_client_cache", "config_from_env", "require_lark_oapi"]
