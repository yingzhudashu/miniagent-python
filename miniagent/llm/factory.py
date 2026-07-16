"""Build an immutable LLM gateway snapshot from the effective JSON configuration."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from miniagent.llm.catalog import ModelCatalog, RoleRouter, model_from_config
from miniagent.llm.gateway import LLMGateway, ProviderRegistry
from miniagent.llm.providers import (
    AnthropicProvider,
    GoogleProvider,
    OpenAIProvider,
)
from miniagent.llm.types import LLMProvider, ProviderConfig

_DRIVER_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
}


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    def thaw(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [thaw(child) for child in item]
        return item

    return {str(key): thaw(child) for key, child in value.items()}


def effective_llm_config(
    getter: Callable[[str, Any], Any],
) -> dict[str, Any]:
    """读取并解冻必需的 LLM 配置段。"""
    value = getter("llm", None)
    if not isinstance(value, Mapping):
        raise ValueError("llm configuration section is required")
    return _mapping(value)


def _provider_configs(llm: Mapping[str, Any]) -> tuple[ProviderConfig, ...]:
    providers = _mapping(llm.get("providers"))
    result = []
    for provider_id, raw_value in providers.items():
        value = _mapping(raw_value)
        driver = str(value.get("driver") or provider_id).strip().lower()
        result.append(
            ProviderConfig(
                provider_id=str(provider_id),
                driver=driver,
                base_url=(str(value["base_url"]).strip() if value.get("base_url") else None),
                credential=(str(value["credential"]) if value.get("credential") else None),
                api_key_env=(str(value["api_key_env"]) if value.get("api_key_env") else None),
                headers={str(k): str(v) for k, v in _mapping(value.get("headers")).items()},
                options=_mapping(value.get("options")),
            )
        )
    return tuple(result)


def _credential_key(
    provider: ProviderConfig,
    secrets: Mapping[str, Any],
) -> str | None:
    llm_secrets = _mapping(secrets.get("llm"))
    credential_id = provider.credential or provider.provider_id
    entry = _mapping(llm_secrets.get(credential_id))
    explicit = entry.get("api_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    env_name = provider.api_key_env or _DRIVER_ENV.get(provider.driver)
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return None


def _build_provider(
    provider: ProviderConfig,
    api_key: str,
    http_timeout: float,
    retries: int,
) -> LLMProvider:
    timeout = float(provider.options.get("timeout", http_timeout))
    max_retries = int(provider.options.get("max_retries", retries))
    if provider.driver == "openai":
        return OpenAIProvider(
            provider.provider_id,
            api_key=api_key,
            base_url=provider.base_url,
            headers=provider.headers,
            timeout=timeout,
            max_retries=max_retries,
        )
    if provider.driver == "anthropic":
        return AnthropicProvider(
            provider.provider_id,
            api_key=api_key,
            base_url=provider.base_url,
            headers=provider.headers,
            timeout=timeout,
            max_retries=max_retries,
        )
    if provider.driver == "google":
        return GoogleProvider(
            provider.provider_id,
            api_key=api_key,
            base_url=provider.base_url,
            headers=provider.headers,
            timeout=timeout,
            max_retries=max_retries,
        )
    raise ValueError(
        f"Unknown LLM provider driver {provider.driver!r} for {provider.provider_id!r}"
    )


def create_llm_gateway(
    getter: Callable[[str, Any], Any],
    *,
    strict_selected: bool = True,
    cache_path: Path | None = None,
) -> LLMGateway:
    """Create providers and role bindings without publishing partial state."""
    llm = effective_llm_config(getter)
    model_values = _mapping(llm.get("models"))
    models = tuple(model_from_config(str(name), _mapping(value)) for name, value in model_values.items())
    catalog = ModelCatalog(models)
    if cache_path is not None:
        from miniagent.llm.catalog_cache import load_catalog_cache

        catalog.load_refreshed(load_catalog_cache(cache_path))
    roles = {str(key): str(value) for key, value in _mapping(llm.get("roles")).items()}
    router = RoleRouter(catalog, roles)
    secrets = _mapping(getter("secrets", {}))
    timeout = float(getter("agent.http_timeout", 120.0))
    retries = int(getter("llm.max_retries", 2))
    providers = []
    missing: dict[str, str] = {}
    for config in _provider_configs(llm):
        api_key = _credential_key(config, secrets)
        if not api_key:
            missing[config.provider_id] = config.api_key_env or _DRIVER_ENV.get(
                config.driver, "API key"
            )
            continue
        providers.append(_build_provider(config, api_key, timeout, retries))
    registry = ProviderRegistry(providers)
    if strict_selected:
        selected = {router.resolve(role).provider for role in RoleRouter._ROLES}
        unavailable = sorted(provider for provider in selected if registry.get(provider) is None)
        if unavailable:
            details = ", ".join(
                f"{provider} ({missing.get(provider, 'not configured')})"
                for provider in unavailable
            )
            raise RuntimeError(f"Selected LLM provider credentials are missing: {details}")
    return LLMGateway(registry, catalog, router, cache_path=cache_path)


__all__ = ["create_llm_gateway", "effective_llm_config"]
