"""飞书 ``lark-oapi`` 客户端工厂与配置读取。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.feishu.types import FeishuConfig

# ─── 客户端缓存（性能优化：避免每次 API 调用重建连接）──

_client_cache: dict[str, Any] = {}


def build_client(config: FeishuConfig) -> Any:
    """获取或复用已缓存的 Lark SDK 客户端（按 app_id 缓存）。"""
    import lark_oapi as lark

    key = config.app_id
    if key not in _client_cache:
        _client_cache[key] = (
            lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        )
    return _client_cache[key]


def clear_client_cache() -> None:
    """清除客户端缓存（测试用）。"""
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
