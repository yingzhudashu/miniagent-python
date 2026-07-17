"""Provider registry and the application-owned protocol-neutral gateway."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

from miniagent.llm.catalog import ModelCatalog, RoleRouter
from miniagent.llm.types import (
    LLMCompletion,
    LLMProvider,
    LLMRole,
    LLMStreamEvent,
    LLMTransportError,
    LLMUsage,
    ModelDescriptor,
)


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if value is not None:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def normalize_usage(usage: Any, model: ModelDescriptor) -> LLMUsage | None:
    """将 provider usage 归一化，并按模型价格计算可选成本。"""
    if usage is None:
        return None
    if isinstance(usage, LLMUsage):
        base = usage
    else:
        input_tokens = _usage_value(usage, "input_tokens", "prompt_tokens")
        output_tokens = _usage_value(usage, "output_tokens", "completion_tokens")
        cache_read = _usage_value(usage, "cache_read_tokens", "cached_tokens")
        cache_write = _usage_value(usage, "cache_write_tokens")
        reasoning = _usage_value(usage, "reasoning_tokens")
        total = _usage_value(usage, "total_tokens") or (
            input_tokens + output_tokens + cache_read + cache_write
        )
        base = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            reasoning_tokens=reasoning,
            total_tokens=total,
        )
    pricing = model.pricing
    rates = (pricing.input, pricing.output, pricing.cache_read, pricing.cache_write)
    if all(rate is None for rate in rates):
        return base
    cost = (
        (pricing.input or 0.0) * base.input_tokens
        + (pricing.output or 0.0) * base.output_tokens
        + (pricing.cache_read or 0.0) * base.cache_read_tokens
        + (pricing.cache_write or 0.0) * base.cache_write_tokens
    ) / 1_000_000
    return LLMUsage(
        input_tokens=base.input_tokens,
        output_tokens=base.output_tokens,
        cache_read_tokens=base.cache_read_tokens,
        cache_write_tokens=base.cache_write_tokens,
        reasoning_tokens=base.reasoning_tokens,
        total_tokens=base.total_tokens,
        cost_usd=cost,
    )


class ProviderRegistry:
    """Explicit provider registry; imports never register network clients."""

    def __init__(self, providers: Iterable[LLMProvider] = ()) -> None:
        self._providers: dict[str, LLMProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: LLMProvider) -> None:
        """按非空 provider id 注册或替换客户端。"""
        provider_id = provider.provider_id.strip()
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> LLMProvider | None:
        """返回已注册 provider，不存在时返回 ``None``。"""
        return self._providers.get(provider_id)

    def require(self, provider_id: str) -> LLMProvider:
        """返回必需 provider，不存在时抛出归一化传输错误。"""
        provider = self.get(provider_id)
        if provider is None:
            raise LLMTransportError(
                f"LLM provider {provider_id!r} is not configured",
                category="provider_unavailable",
            )
        return provider

    def all(self) -> tuple[LLMProvider, ...]:
        """返回当前 provider 快照。"""
        return tuple(self._providers.values())

    async def close(self) -> None:
        """并发关闭全部 provider，并聚合关闭异常。"""
        results = await asyncio.gather(
            *(provider.close() for provider in self._providers.values()),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise RuntimeError(
                f"{len(errors)} LLM provider(s) failed to close: {errors[0]}"
            ) from errors[0]


class LLMGateway:
    """Route explicit role/profile requests to one provider snapshot."""

    def __init__(
        self,
        registry: ProviderRegistry,
        catalog: ModelCatalog,
        router: RoleRouter,
        cache_path: Path | None = None,
    ) -> None:
        self.registry = registry
        self.catalog = catalog
        self.router = router
        self.cache_path = cache_path
        self._closed = False
        self._ready = False
        self.last_usage: LLMUsage | None = None

    async def initialize(self) -> None:
        """Validate the immutable provider/catalog snapshot without network I/O."""
        self._ensure_open()
        for role in ("default", "reasoning", "fast", "vision"):
            model = self.router.resolve(role)
            self.registry.require(model.provider)

    async def start(self) -> None:
        """Mark the validated gateway ready for an AgentRuntime."""
        await self.initialize()
        self._ready = True

    async def stop(self) -> None:
        """Lifecycle alias for :meth:`close`."""
        await self.close()

    def health(self) -> dict[str, Any]:
        """Return a provider-neutral, non-blocking health snapshot."""
        return {
            "ready": self._ready and not self._closed,
            "closed": self._closed,
            "providers": tuple(provider.provider_id for provider in self.registry.all()),
            "models": tuple(model.profile for model in self.catalog.all()),
        }

    def model_for_role(self, role: LLMRole = "default") -> ModelDescriptor:
        """返回指定角色当前绑定的模型档案。"""
        return self.router.resolve(role)

    def _request_model(
        self,
        role: LLMRole,
        profile: str | None,
    ) -> ModelDescriptor:
        if profile:
            model = self.catalog.get(str(profile))
            if model is None:
                raise LLMTransportError(
                    f"Unknown model profile: {profile}", category="model_not_found"
                )
            return model
        return self.router.resolve(role)

    @staticmethod
    def _provider_params(params: dict[str, Any], model: ModelDescriptor) -> dict[str, Any]:
        clean = dict(model.defaults)
        clean.update({
            key: value
            for key, value in params.items()
            if not key.startswith("_") and key not in {"model"}
        })
        clean["model"] = model.model
        clean["max_tokens"] = min(
            int(clean.get("max_tokens", model.max_output_tokens)), model.max_output_tokens
        )
        compatibility = dict(model.compatibility)
        omitted = {str(name) for name in compatibility.get("omit_parameters", ())}
        omitted.update(str(name) for name in params.get("_omit_parameters", ()))
        for name in omitted:
            clean.pop(name, None)
        if compatibility.get("supports_temperature") is False:
            clean.pop("temperature", None)
        if compatibility.get("supports_top_p") is False:
            clean.pop("top_p", None)
        parameter_map = compatibility.get("parameter_map")
        if isinstance(parameter_map, dict):
            for source, target in parameter_map.items():
                if source in clean:
                    clean[str(target)] = clean.pop(source)
        extra_body = compatibility.get("extra_body")
        if isinstance(extra_body, dict):
            clean["extra_body"] = {**clean.get("extra_body", {}), **extra_body}
        # Thinking controls are provider-neutral MiniAgent semantics. Model defaults use
        # public JSON names, while transports consume underscored internal names so raw
        # ``thinking_level`` / ``thinking_budget`` never leak into provider SDK calls.
        for name in ("thinking_level", "thinking_budget"):
            internal_name = f"_{name}"
            default_value = clean.pop(name, None)
            if name in omitted or internal_name in omitted:
                clean.pop(internal_name, None)
                continue
            if internal_name in params:
                clean[internal_name] = params[internal_name]
            elif internal_name not in clean and default_value is not None:
                clean[internal_name] = default_value
        if compatibility.get("thinking_adapter") == "qwen":
            level = str(clean.get("_thinking_level") or "").strip().lower()
            budget = int(clean.get("_thinking_budget") or 0)
            qwen_body: dict[str, Any] = {
                "enable_thinking": level not in {"", "none", "disabled", "off"}
            }
            if qwen_body["enable_thinking"] and budget > 0:
                qwen_body["thinking_budget"] = budget
            clean["extra_body"] = {**clean.get("extra_body", {}), **qwen_body}
        return clean

    async def create_completion(
        self,
        *,
        role: LLMRole = "default",
        profile: str | None = None,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        """路由一次非流式请求并归一化返回用量。"""
        self._ensure_open()
        model = self._request_model(role, profile)
        provider = self.registry.require(model.provider)
        response = await provider.create_completion(
            model,
            messages=messages,
            params=self._provider_params(params, model),
            tools=tools,
            json_mode=json_mode,
        )
        response.usage = normalize_usage(response.usage, model)
        self.last_usage = response.usage
        return response

    async def stream_completion(
        self,
        *,
        role: LLMRole = "default",
        profile: str | None = None,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        """路由流式请求并逐事件归一化用量。"""
        self._ensure_open()
        model = self._request_model(role, profile)
        provider = self.registry.require(model.provider)
        async for event in provider.stream_completion(
            model,
            messages=messages,
            params=self._provider_params(params, model),
            tools=tools,
            json_mode=json_mode,
        ):
            if event.usage is not None:
                event.usage = normalize_usage(event.usage, model)
                self.last_usage = event.usage
            yield event

    async def refresh(self, provider_id: str | None = None) -> None:
        """刷新一个或全部 provider 的 last-known-good 模型目录。"""
        providers = (
            (self.registry.require(provider_id),)
            if provider_id is not None
            else self.registry.all()
        )
        for provider in providers:
            try:
                models = await provider.list_models()
            except Exception:
                if provider_id is not None:
                    raise
                continue
            self.catalog.replace_refreshed(provider.provider_id, models)
        if self.cache_path is not None:
            from miniagent.llm.catalog_cache import save_catalog_cache

            save_catalog_cache(self.cache_path, self.catalog.refreshed())

    async def close(self) -> None:
        """幂等关闭网关拥有的全部 provider。"""
        if self._closed:
            return
        self._closed = True
        self._ready = False
        await self.registry.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise LLMTransportError(
                "LLM gateway is closed", category="provider_unavailable"
            )


__all__ = ["LLMGateway", "ProviderRegistry", "normalize_usage"]
