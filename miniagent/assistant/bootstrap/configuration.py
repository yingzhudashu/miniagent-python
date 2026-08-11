"""Application use cases for publishing configuration and loading secrets."""

from __future__ import annotations

import os

from miniagent.agent.logging import get_logger
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.infrastructure.json_config import (
    get_config_paths,
    get_config_section,
    get_configuration_service,
    install_configuration_service,
)

_logger = get_logger(__name__)
_SECRETS_TO_ENV = {
    "tavily_api_key": "TAVILY_API_KEY",
    "web_search_api_key": "WEB_SEARCH_API_KEY",
    "stack_exchange_key": "STACK_EXCHANGE_KEY",
    "embed_api_key": "MINIAGENT_EMBED_API_KEY",
    "feishu_app_id": "FEISHU_APP_ID",
    "feishu_app_secret": "FEISHU_APP_SECRET",
    "feishu_verification_token": "FEISHU_VERIFICATION_TOKEN",
    "feishu_encrypt_key": "FEISHU_ENCRYPT_KEY",
    "feishu_user_access_token": "MINIAGENT_FEISHU_USER_ACCESS_TOKEN",
    "github_token": "GITHUB_TOKEN",
}


def load_secrets_from_config() -> None:
    """Publish configured secrets to the SDK environment boundary."""
    try:
        secrets = get_config_section("secrets")
        for secret_key, env_name in _SECRETS_TO_ENV.items():
            value = secrets.get(secret_key)
            if isinstance(value, str) and value.strip():
                os.environ[env_name] = value.strip()
    except Exception as error:
        _logger.warning("加载 secrets 失败: %s", error)


def load_secrets_from_project_root() -> None:
    load_secrets_from_config()


async def reload_runtime_config(container: ApplicationContainer) -> None:
    """Validate and atomically publish the current runtime configuration."""
    candidate = get_configuration_service().reloaded(strict=True)
    from miniagent.llm.factory import create_llm_gateway

    replacement = create_llm_gateway(
        candidate.get,
        cache_path=get_config_paths()[1].parent / "llm-model-catalog.json",
    )
    previous = container.llm_gateway
    install_configuration_service(candidate)
    load_secrets_from_project_root()
    container.config = candidate
    container.llm_gateway = replacement
    if previous is not None and previous is not replacement:
        container.retired_llm_gateways.append(previous)


__all__ = [
    "load_secrets_from_config",
    "load_secrets_from_project_root",
    "reload_runtime_config",
]
