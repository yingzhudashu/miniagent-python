"""飞书 ``lark-oapi`` 客户端工厂与配置读取。"""

from __future__ import annotations

import os
from typing import Any

from miniagent.feishu.types import FeishuConfig


def config_from_env() -> FeishuConfig | None:
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


def build_client(config: FeishuConfig) -> Any:
    import lark_oapi as lark

    return lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()


def require_lark_oapi() -> None:
    import lark_oapi  # noqa: F401


__all__ = ["build_client", "config_from_env", "require_lark_oapi"]
