"""加载敏感凭据到环境变量

从config.user.json的secrets部分读取敏感凭据并设置到环境变量。
支持的环境变量：
- OPENAI_API_KEY
- TAVILY_API_KEY
- WEB_SEARCH_API_KEY
- MINIAGENT_EMBED_API_KEY
- FEISHU_APP_ID
- FEISHU_APP_SECRET
- FEISHU_VERIFICATION_TOKEN
- FEISHU_ENCRYPT_KEY
- MINIAGENT_FEISHU_USER_ACCESS_TOKEN
- GITHUB_TOKEN

配置优先级：环境变量已有值 > config.user.json secrets
"""

from __future__ import annotations

import os

from miniagent.infrastructure.json_config import JsonConfigLoader
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# secrets字段到环境变量名的映射
_SECRETS_TO_ENV = {
    "openai_api_key": "OPENAI_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
    "web_search_api_key": "WEB_SEARCH_API_KEY",
    "embed_api_key": "MINIAGENT_EMBED_API_KEY",
    "feishu_app_id": "FEISHU_APP_ID",
    "feishu_app_secret": "FEISHU_APP_SECRET",
    "feishu_verification_token": "FEISHU_VERIFICATION_TOKEN",
    "feishu_encrypt_key": "FEISHU_ENCRYPT_KEY",
    "feishu_user_access_token": "MINIAGENT_FEISHU_USER_ACCESS_TOKEN",
    "github_token": "GITHUB_TOKEN",
}


def load_secrets_from_config() -> None:
    """从config.user.json的secrets部分读取凭据并设置环境变量。

    只在环境变量未设置时才设置（环境变量优先级更高）。
    """
    try:
        config = JsonConfigLoader.get_instance()
        secrets = config.get_section("secrets")

        if not secrets:
            _logger.debug("config.user.json中无secrets部分")
            return

        for secret_key, env_name in _SECRETS_TO_ENV.items():
            # 只在环境变量未设置时才设置
            if env_name not in os.environ or not os.environ[env_name]:
                value = secrets.get(secret_key)
                if value and isinstance(value, str) and value.strip():
                    os.environ[env_name] = value.strip()
                    _logger.debug(f"从config.user.json设置环境变量: {env_name}")

        _logger.debug("已从config.user.json加载secrets部分")

    except Exception as e:
        _logger.warning(f"加载secrets失败: {e}")


def load_secrets_from_project_root() -> None:
    """加载项目根目录的敏感凭据。

    从config.user.json的secrets部分读取凭据并设置到环境变量。
    """
    load_secrets_from_config()


__all__ = ["load_secrets_from_project_root", "load_secrets_from_config"]