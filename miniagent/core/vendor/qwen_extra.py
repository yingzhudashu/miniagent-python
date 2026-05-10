"""供应商扩展：DashScope / Qwen OpenAI 兼容端点的 chat.completions extra_body。"""

from __future__ import annotations

from typing import Any


def build_thinking_extra_body(
    base_url: str,
    thinking_level: str,
    thinking_budget: int,
    *,
    model_overrides_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回应传入 AsyncOpenAI chat.completions.create(..., extra_body=...) 的 dict。

    非 Qwen/DashScope 端点返回空 dict；用户可通过 model_overrides['extra_body'] 完全覆盖。
    """
    merged: dict[str, Any] = {}
    if model_overrides_extra and isinstance(model_overrides_extra, dict):
        eb = model_overrides_extra.get("extra_body")
        if isinstance(eb, dict):
            merged.update(eb)

    bu = (base_url or "").lower()
    if not any(x in bu for x in ("dashscope", "aliyuncs", "coding.dashscope")):
        return merged

    # 通义 OpenAI 兼容常见字段（随网关文档可调整）
    if thinking_budget and thinking_budget > 0:
        merged.setdefault("enable_thinking", True)
        merged.setdefault("thinking_budget", thinking_budget)
    else:
        merged.setdefault("enable_thinking", False)

    tl = (thinking_level or "light").lower()
    if tl == "disabled":
        merged["enable_thinking"] = False
    return merged


__all__ = ["build_thinking_extra_body"]
