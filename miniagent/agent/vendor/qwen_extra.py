"""供应商扩展：DashScope / Qwen OpenAI 兼容端点的 chat.completions extra_body。"""

from __future__ import annotations

from typing import Any

# base_url 子串匹配，识别 Qwen/DashScope OpenAI 兼容端点。
_QWEN_BASE_URL_MARKERS = ("dashscope", "aliyuncs", "coding.dashscope")

# 关闭 thinking 的档位名（与 ModelConfig 文档中的 ``none`` 及历史 ``disabled`` 对齐）。
_THINKING_DISABLED_LEVELS = frozenset({"disabled", "none"})


def _is_qwen_compatible_base_url(base_url: str | None) -> bool:
    bu = (base_url or "").lower()
    return any(marker in bu for marker in _QWEN_BASE_URL_MARKERS)


def build_thinking_extra_body(
    base_url: str,
    thinking_level: str,
    thinking_budget: int,
    *,
    model_overrides_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 ``chat.completions.create(..., extra_body=...)`` 的 Qwen thinking 字段。

    合并顺序：
    1. 先合并 ``model_overrides_extra['extra_body']``（若存在）。
    2. 非 Qwen 端点到此结束，不注入 thinking 字段。
    3. Qwen 端点：``thinking_level`` 为 ``none`` / ``disabled`` 时强制关闭 thinking；
       否则 ``thinking_budget > 0`` 时用 ``setdefault`` 补 ``enable_thinking`` /
       ``thinking_budget``（不覆盖用户已在 extra_body 中设置的值）；``thinking_budget <= 0``
       时强制 ``enable_thinking = False``。

    ``thinking_level`` 的 ``light`` / ``medium`` / ``heavy`` 等档位不映射到 Qwen API 字段，
    深度由上游 ``thinking_presets`` 换算后的 ``thinking_budget`` 控制。

    Args:
        base_url: 模型 API 端点 URL，用于判断是否 DashScope/Qwen 兼容网关。
        thinking_level: 思考档位；``none`` / ``disabled`` 关闭 thinking，其余值仅作语义保留。
        thinking_budget: 思考 token 预算；``<= 0`` 时关闭 thinking。
        model_overrides_extra: 可选 ``model_overrides`` 字典；其中的 ``extra_body`` 会先合并。

    Returns:
        应传入 ``extra_body`` 的字典。无用户 ``extra_body`` 且非 Qwen 端点时为 ``{}``。
    """
    merged: dict[str, Any] = {}
    if model_overrides_extra and isinstance(model_overrides_extra, dict):
        eb = model_overrides_extra.get("extra_body")
        if isinstance(eb, dict):
            merged.update(eb)

    if not _is_qwen_compatible_base_url(base_url):
        return merged

    tl = (thinking_level or "light").lower()
    if tl in _THINKING_DISABLED_LEVELS:
        merged["enable_thinking"] = False
        return merged

    if thinking_budget > 0:
        merged.setdefault("enable_thinking", True)
        merged.setdefault("thinking_budget", thinking_budget)
    else:
        merged["enable_thinking"] = False

    return merged


__all__ = ["build_thinking_extra_body"]
