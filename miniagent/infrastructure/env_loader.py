"""加载项目根目录 ``.env``（幂等，不覆盖已存在的进程环境变量）。"""

from __future__ import annotations

import os

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)
_warned_removed_external_config = False

_REMOVED_EXTERNAL_CONFIG_KEYS = ("MINIAGENT_CONFIG", "MINIAGENT_OPENCLAW_CONFIG")


def load_dotenv_from_project_root() -> None:
    try:
        from dotenv import load_dotenv

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(root, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass
    _warn_removed_external_config_env()


def _warn_removed_external_config_env() -> None:
    """若仍设置已移除的外部 JSON 路径 env，打一次性 WARNING。"""
    global _warned_removed_external_config
    if _warned_removed_external_config:
        return
    found = [k for k in _REMOVED_EXTERNAL_CONFIG_KEYS if (os.environ.get(k) or "").strip()]
    if not found:
        return
    _warned_removed_external_config = True
    keys = "、".join(found)
    _logger.warning(
        "%s 已不再支持（外部 JSON 配置已移除）。请改用 .env 扁平变量，"
        "OpenClaw 字段映射见项目根 .env.example §2。",
        keys,
    )


def reset_removed_external_config_warnings_for_tests() -> None:
    """单测用：清空已移除外部 JSON env 警告去重标志。"""
    global _warned_removed_external_config
    _warned_removed_external_config = False
