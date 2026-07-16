"""Model profile selector used by the TUI overlay command."""

from __future__ import annotations

from typing import Any


async def choose_model_profile(ctx: Any) -> str | None:
    """展示当前可用 provider 的模型档案选择对话框。"""
    gateway = getattr(ctx, "llm_gateway", None)
    if gateway is None:
        return None
    models = tuple(
        model
        for model in gateway.catalog.all()
        if gateway.registry.get(model.provider) is not None
    )
    if not models:
        return None
    from prompt_toolkit.shortcuts import radiolist_dialog

    values = []
    for model in models:
        flags = []
        if model.capabilities.reasoning:
            flags.append("推理")
        if model.capabilities.vision:
            flags.append("视觉")
        if model.capabilities.tools:
            flags.append("工具")
        suffix = f" [{' / '.join(flags)}]" if flags else ""
        values.append((model.profile, f"{model.provider} · {model.model}{suffix}"))
    dialog = radiolist_dialog(
        title="选择默认回答模型",
        text="切换从下一轮生效；活动请求继续使用原模型。",
        values=values,
        ok_text="切换",
        cancel_text="取消",
    )
    return await dialog.run_async()


__all__ = ["choose_model_profile"]
