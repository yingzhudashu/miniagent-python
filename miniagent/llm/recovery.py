"""Provider-neutral completion failure classification and bounded retry policy."""

from __future__ import annotations

from typing import Any

from miniagent.llm.capabilities import unsupported_parameter_names
from miniagent.llm.types import LLMCompletion, LLMFailureInfo


def classify_transport_error(error: Exception) -> LLMFailureInfo:
    """按状态码和安全错误特征判断失败类别与可重试性。"""
    status_raw = getattr(error, "status_code", None)
    try:
        status = int(status_raw) if status_raw is not None else None
    except (TypeError, ValueError):
        status = None
    message = str(error).lower()
    if unsupported_parameter_names(error):
        return LLMFailureInfo("unsupported_parameter", True, status)
    deterministic_markers = (
        "invalid api key",
        "incorrect api key",
        "authentication",
        "permission denied",
        "model_not_found",
        "model does not exist",
        "no_available_providers",
        "cloudflare/waf",
        "http 403",
    )
    if status in (401, 403) or any(marker in message for marker in deterministic_markers):
        return LLMFailureInfo("deterministic_api_error", False, status)
    if status == 404 and ("model" in message or "not found" in message):
        return LLMFailureInfo("deterministic_api_error", False, status)
    generic_invalid_request = status == 400 and (
        "invalid_request_error" in message
        or "cch_session_id" in message
        or "上游请求参数无效" in message
    )
    if generic_invalid_request or status == 429 or bool(status and status >= 500):
        return LLMFailureInfo("transient_api_error", True, status)
    if status is None:
        return LLMFailureInfo("network_error", True, None)
    return LLMFailureInfo("api_error", False, status)


def completion_failure_category(completion: LLMCompletion) -> str | None:
    """识别无可用正文 completion 的结构化失败类别。"""
    if (completion.content or "").strip():
        return None
    output_types = set(completion.output_item_types)
    if completion.status == "incomplete":
        return "incomplete_output"
    if completion.status == "failed":
        return "failed_response"
    if output_types and output_types <= {"reasoning"}:
        return "reasoning_only"
    if completion.status == "completed":
        return "completed_without_text"
    return "empty_gateway_response"


def structured_retry_params(
    current: dict[str, Any],
    *,
    next_attempt: int,
    max_attempts: int,
    final_reasoning: str,
    model_max_tokens: int,
    incomplete_reason: str | None = None,
) -> dict[str, Any]:
    """为下一次结构化恢复请求生成有界兼容参数。"""
    recovered = dict(current)
    recovered.pop("temperature", None)
    recovered.pop("top_p", None)
    recovered["_omit_parameters"] = ("temperature", "top_p")
    if next_attempt == max_attempts:
        recovered["_thinking_level"] = final_reasoning
    normalized_reason = str(incomplete_reason or "").strip().lower()
    if any(
        marker in normalized_reason
        for marker in ("max_output_tokens", "max_tokens", "token_limit", "length")
    ):
        current_budget = int(recovered.get("max_tokens", 0) or 0)
        if current_budget > 0 and model_max_tokens > current_budget:
            recovered["max_tokens"] = min(current_budget * 2, model_max_tokens)
    return recovered


def structured_retry_delay(next_attempt: int) -> float:
    """返回短且有界的结构化恢复退避时间。"""
    return 0.2 if next_attempt == 2 else 0.5


__all__ = [
    "classify_transport_error",
    "completion_failure_category",
    "structured_retry_delay",
    "structured_retry_params",
]
