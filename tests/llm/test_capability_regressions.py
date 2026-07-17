"""Focused regressions migrated from test_diff_gate_new_modules.py."""

from __future__ import annotations

import pytest

from miniagent.llm import capabilities as llm_capabilities


class _CapabilityClient:
    """可弱引用的能力缓存测试客户端。"""

    def __init__(self, base_url: str = "https://gateway.example") -> None:
        self.base_url = base_url

class _GatewayError(Exception):
    """携带兼容 HTTP 状态码的供应商错误。"""

    def __init__(self, message: str, status_code: int | str) -> None:
        super().__init__(message)
        self.status_code = status_code

def test_llm_capability_detection_weak_and_fallback_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_capabilities._CLIENT_BUCKETS.clear()
    llm_capabilities._FALLBACK.clear()
    assert llm_capabilities.unsupported_parameter_names(_GatewayError("bad", 500)) == set()
    assert llm_capabilities.unsupported_parameter_names(
        _GatewayError("temperature parameter is not supported", "400")
    ) == {"temperature"}

    events: list[dict[str, object]] = []
    monkeypatch.setattr("miniagent.agent.observability.emit_trace", events.append)
    client = _CapabilityClient()
    params = {"model": "m", "temperature": 0.2, "top_p": 0.9, "keep": True}
    llm_capabilities.learn_unsupported_params(
        client,
        params,
        "responses",
        _GatewayError("unsupported parameter: temperature", 400),
    )
    adjusted, removed = llm_capabilities.apply_learned_capabilities(
        client, params, "responses"
    )
    assert removed == ("temperature",)
    assert adjusted == {"model": "m", "top_p": 0.9, "keep": True}
    assert events == []  # LLM capability learning stays independent from Agent tracing.

    fallback_client: object = object()
    llm_capabilities.learn_unsupported_params(
        fallback_client,
        params,
        "chat_completions",
        _GatewayError("top_p is not supported", 400),
    )
    adjusted, removed = llm_capabilities.apply_learned_capabilities(
        fallback_client, params, "chat_completions"
    )
    assert removed == ("top_p",) and "top_p" not in adjusted
    untouched, removed = llm_capabilities.apply_learned_capabilities(
        _CapabilityClient("other"), params, "responses"
    )
    assert untouched is params and removed == ()

def test_llm_capability_buckets_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_capabilities._CLIENT_BUCKETS.clear()
    llm_capabilities._FALLBACK.clear()
    monkeypatch.setattr(llm_capabilities, "_CLIENT_MAX", 1)
    monkeypatch.setattr(llm_capabilities, "_FALLBACK_MAX", 1)
    error = _GatewayError("temperature not supported", 400)
    client = _CapabilityClient()
    for model in ("first", "second"):
        llm_capabilities.learn_unsupported_params(
            client, {"model": model, "temperature": 1}, "responses", error
        )
    assert len(llm_capabilities._CLIENT_BUCKETS[client]) == 1
    for fallback in (object(), object()):
        llm_capabilities.learn_unsupported_params(
            fallback, {"model": "m", "temperature": 1}, "responses", error
        )
    assert len(llm_capabilities._FALLBACK) == 1
