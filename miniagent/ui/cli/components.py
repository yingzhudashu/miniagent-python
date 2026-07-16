"""Responsive footer and status formatting for the full-screen CLI."""

from __future__ import annotations

from typing import Any


def _compact_tokens(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.1f}k" if value < 10_000 else f"{value // 1_000}k"
    return f"{value / 1_000_000:.1f}M"


def _active_model(ctx: Any) -> tuple[str, str, int]:
    gateway = getattr(ctx, "llm_gateway", None)
    if gateway is None:
        return "未配置", "no-model", 0
    try:
        model = gateway.model_for_role("default")
    except Exception:
        return "未配置", "no-model", 0
    return model.provider, model.model, model.context_window


def _context_estimate(state: Any) -> int:
    try:
        return max(0, int(state.get("context_tokens_used", 0)))
    except (AttributeError, TypeError, ValueError):
        return 0


def footer_text(ctx: Any, state: Any, view: Any, width: int) -> str:
    """Build one stable-width footer with graceful field shedding."""
    provider, model, context_window = _active_model(ctx)
    session = str(state.get("active_session_id") or "default")
    used = _context_estimate(state)
    context = (
        f"上下文 {_compact_tokens(used)}/{_compact_tokens(context_window)}"
        if context_window
        else "上下文 —"
    )
    thinking = "推理展开" if view.reasoning_expanded else "推理折叠"
    queue = f"队列 {view.queued_messages}" if view.queued_messages else ""
    gateway = getattr(ctx, "llm_gateway", None)
    usage = getattr(gateway, "last_usage", None)
    usage_text = ""
    if usage is not None:
        usage_text = f"本轮 {_compact_tokens(usage.total_tokens)} tok"
        if usage.cost_usd is not None:
            usage_text += f" ${usage.cost_usd:.4f}"
    left_parts = [session, view.status, context, usage_text]
    right_parts = [f"{provider}/{model}", thinking, queue]
    left = " · ".join(part for part in left_parts if part)
    right = " · ".join(part for part in right_parts if part)
    if len(left) + len(right) + 2 <= width:
        return left + " " * max(2, width - len(left) - len(right)) + right
    compact = f"{session} · {view.status}  {provider}/{model}"
    return compact[: max(1, width - 1)]


__all__ = ["footer_text"]
