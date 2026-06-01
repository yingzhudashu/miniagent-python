"""加载敏感凭据环境变量

只加载.env.secrets（敏感凭据），不再支持老的.env格式。
所有配置通过JSON格式传递：
- config.defaults.json - 默认配置
- config.user.json - 用户配置
- MINIAGENT_CONFIG环境变量 - JSON格式运行时配置
"""

from __future__ import annotations

import os

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


def load_secrets_from_project_root() -> None:
    """加载项目根目录的敏感凭据文件

    只加载.env.secrets（API密钥等敏感信息）。
    不再加载老的.env文件。
    """
    try:
        from dotenv import load_dotenv

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # 只加载 .env.secrets（敏感凭据）
        secrets_path = os.path.join(root, ".env.secrets")
        if os.path.exists(secrets_path):
            load_dotenv(secrets_path, override=False)
            _logger.debug("已加载 .env.secrets（敏感凭据）")

    except ImportError:
        pass


__all__ = ["load_secrets_from_project_root"]