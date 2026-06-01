"""飞书 IM / 云文档内置工具：环境策略与启动自检日志。"""

from __future__ import annotations

import os

from miniagent.infrastructure.env_parse import env_flag
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

_logged_startup_hint = False


def feishu_credentials_configured() -> bool:
    """进程环境中是否同时存在飞书应用 ID 与 Secret（与长轮询共用变量）。"""
    aid = (os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    return bool(aid and sec)


def feishu_im_tools_should_register() -> bool:
    """是否注册 ``feishu_*`` 内置工具。

    - 配置 feishu.tools_explicit=true → 开启。
    - 配置 feishu.tools_explicit=false → **关闭**（优先于 AUTO）。
    - 未设置时：若 feishu.tools_auto 为真且已配置 App ID/Secret → 开启。
    """
    explicit = get_config("feishu.tools_explicit", None)
    if explicit is not None:
        return bool(explicit)
    if not get_config("feishu.tools_auto", True):
        return False
    return feishu_credentials_configured()


def log_feishu_im_tools_startup_hint_once() -> None:
    """飞书长轮询即将连接时：若凭证齐全但未注册扩展工具，打一条 INFO 指向文档。"""
    global _logged_startup_hint
    if _logged_startup_hint:
        return
    _logged_startup_hint = True
    if not feishu_credentials_configured():
        return
    if feishu_im_tools_should_register():
        return
    _logger.info(
        "飞书扩展内置工具未注册：Agent 无法使用 feishu_doc / feishu_bitable / 发文件等内置工具。"
        " 请设置 MINIAGENT_FEISHU_TOOLS=1，或不要设置 MINIAGENT_FEISHU_TOOLS 且设置 "
        "MINIAGENT_FEISHU_TOOLS_AUTO=1（并已配置 FEISHU_APP_ID/SECRET）；并参阅 docs/FEISHU.md"
        "「飞书工具与 IM 自检清单」。"
    )


def reset_feishu_im_tools_startup_hint_for_tests() -> None:
    """单测用：重置启动提示去重标记。"""
    global _logged_startup_hint
    _logged_startup_hint = False


__all__ = [
    "feishu_credentials_configured",
    "feishu_im_tools_should_register",
    "log_feishu_im_tools_startup_hint_once",
    "reset_feishu_im_tools_startup_hint_for_tests",
]
