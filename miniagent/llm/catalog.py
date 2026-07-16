"""Small verified model catalog and explicit role routing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from miniagent.llm.types import (
    LLMRole,
    ModelCapabilities,
    ModelDescriptor,
    ModelPricing,
)


def _builtin_models() -> tuple[ModelDescriptor, ...]:
    return (
        ModelDescriptor(
            profile="openai-gpt-4o-mini",
            provider="openai",
            model="gpt-4o-mini",
            api="openai_responses",
            display_name="GPT-4o mini",
            context_window=128_000,
            max_output_tokens=16_384,
            capabilities=ModelCapabilities(tools=True, vision=True, structured_output=True),
        ),
        ModelDescriptor(
            profile="anthropic-sonnet",
            provider="anthropic",
            model="claude-sonnet-4-5",
            api="anthropic_messages",
            display_name="Claude Sonnet",
            context_window=200_000,
            max_output_tokens=16_384,
            capabilities=ModelCapabilities(
                tools=True, vision=True, reasoning=True, structured_output=True
            ),
        ),
        ModelDescriptor(
            profile="google-gemini-flash",
            provider="google",
            model="gemini-2.5-flash",
            api="google_generate_content",
            display_name="Gemini Flash",
            context_window=1_000_000,
            max_output_tokens=65_536,
            capabilities=ModelCapabilities(
                tools=True, vision=True, reasoning=True, structured_output=True
            ),
        ),
    )


class ModelCatalog:
    """Merge built-in, refreshed and user model profiles with stable precedence."""

    def __init__(self, models: Iterable[ModelDescriptor] = ()) -> None:
        self._builtin = {model.profile: model for model in _builtin_models()}
        self._refreshed: dict[str, ModelDescriptor] = {}
        self._user = {model.profile: model for model in models}

    def get(self, profile: str) -> ModelDescriptor | None:
        """按用户、动态刷新、内置的优先级查找模型档案。"""
        return self._user.get(profile) or self._refreshed.get(profile) or self._builtin.get(profile)

    def all(self, provider: str | None = None) -> tuple[ModelDescriptor, ...]:
        """返回稳定排序的有效模型目录，可按 provider 过滤。"""
        merged = {**self._builtin, **self._refreshed, **self._user}
        values: Iterable[ModelDescriptor] = merged.values()
        if provider is not None:
            values = (model for model in values if model.provider == provider)
        return tuple(sorted(values, key=lambda model: (model.provider, model.profile)))

    def replace_refreshed(self, provider: str, models: Iterable[ModelDescriptor]) -> None:
        """Replace only one provider's last-known dynamic entries after success."""
        self._refreshed = {
            profile: model
            for profile, model in self._refreshed.items()
            if model.provider != provider
        }
        self._refreshed.update({model.profile: model for model in models})

    def load_refreshed(self, models: Iterable[ModelDescriptor]) -> None:
        """加载持久化的 last-known-good 动态模型条目。"""
        self._refreshed.update({model.profile: model for model in models})

    def refreshed(self) -> tuple[ModelDescriptor, ...]:
        """返回适合持久化的动态模型条目快照。"""
        return tuple(sorted(self._refreshed.values(), key=lambda model: model.profile))


class RoleRouter:
    """Resolve explicit stage roles without automatic cross-provider selection."""

    _ROLES: tuple[LLMRole, ...] = ("default", "reasoning", "fast", "vision")

    def __init__(self, catalog: ModelCatalog, bindings: Mapping[str, str]) -> None:
        self.catalog = catalog
        self.bindings = {str(key): str(value) for key, value in bindings.items()}

    def resolve(self, role: LLMRole = "default") -> ModelDescriptor:
        """解析角色绑定并校验模型能力。"""
        profile = self.bindings.get(role) or self.bindings.get("default")
        if not profile:
            profile = "openai-gpt-4o-mini"
        model = self.catalog.get(profile)
        if model is None:
            raise ValueError(f"Unknown LLM model profile for role {role!r}: {profile!r}")
        self._validate_role(role, model)
        return model

    @staticmethod
    def _validate_role(role: LLMRole, model: ModelDescriptor) -> None:
        if role == "vision" and not model.capabilities.vision:
            raise ValueError(
                f"Model profile {model.profile!r} cannot serve the vision role"
            )


def model_from_config(profile: str, value: Mapping[str, Any]) -> ModelDescriptor:
    """Parse one model profile from the v3 JSON shape."""
    capabilities = value.get("capabilities") or {}
    pricing = value.get("pricing") or {}
    return ModelDescriptor(
        profile=profile,
        provider=str(value.get("provider") or "").strip(),
        model=str(value.get("model") or "").strip(),
        api=str(value.get("api") or "openai_chat"),  # type: ignore[arg-type]
        display_name=(str(value["display_name"]) if value.get("display_name") else None),
        context_window=int(value.get("context_window", 128_000)),
        max_output_tokens=int(value.get("max_output_tokens", value.get("max_tokens", 4_096))),
        capabilities=ModelCapabilities(
            tools=bool(capabilities.get("tools", True)),
            vision=bool(capabilities.get("vision", False)),
            reasoning=bool(capabilities.get("reasoning", False)),
            structured_output=bool(capabilities.get("structured_output", True)),
        ),
        pricing=ModelPricing(
            input=_optional_float(pricing.get("input")),
            output=_optional_float(pricing.get("output")),
            cache_read=_optional_float(pricing.get("cache_read")),
            cache_write=_optional_float(pricing.get("cache_write")),
        ),
        defaults=dict(value.get("defaults") or {}),
        compatibility=dict(value.get("compatibility") or {}),
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def with_provider_profile(model: ModelDescriptor, provider: str, profile: str) -> ModelDescriptor:
    """Give dynamically discovered models stable provider-scoped profile ids."""
    return replace(model, provider=provider, profile=profile)


__all__ = ["ModelCatalog", "RoleRouter", "model_from_config", "with_provider_profile"]
