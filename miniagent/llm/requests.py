"""Current Gateway request helpers shared by Agent control phases."""

from __future__ import annotations

import json
from typing import Any

from miniagent.llm.gateway import LLMGateway
from miniagent.llm.types import (
    LLMCompletion,
    LLMFunctionCall,
    LLMRole,
    LLMToolCall,
)


def _tool_call(call_id: str, name: str, arguments: str) -> LLMToolCall:
    try:
        parsed = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return LLMToolCall(
        id=call_id,
        function=LLMFunctionCall(name=name, arguments=arguments or "{}"),
        _args_dict=parsed if isinstance(parsed, dict) else {},
    )


async def create_structured_completion(
    gateway: LLMGateway,
    *,
    role: LLMRole,
    profile: str | None = None,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
) -> LLMCompletion:
    """Use streaming aggregation for Responses and JSON mode elsewhere."""
    model = gateway.catalog.get(profile) if profile else gateway.model_for_role(role)
    if model is None:
        raise ValueError(f"Unknown model profile: {profile}")
    if model.api != "openai_responses":
        return await gateway.create_completion(
            role=role,
            profile=profile,
            messages=messages,
            params=params,
            json_mode=True,
        )

    fragments: list[str] = []
    call_state: dict[int, dict[str, str]] = {}
    usage: Any | None = None
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    response_model: str | None = None
    async for event in gateway.stream_completion(
        role=role,
        profile=profile,
        messages=messages,
        params=params,
        json_mode=True,
    ):
        if event.content_delta:
            fragments.append(event.content_delta)
        usage = event.usage if event.usage is not None else usage
        status = event.status if event.status is not None else status
        output_item_types = event.output_item_types or output_item_types
        incomplete_reason = event.incomplete_reason or incomplete_reason
        response_model = event.model or response_model
        delta = event.tool_call_delta
        if delta is not None:
            state = call_state.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
            state["id"] = delta.id or state["id"]
            state["name"] = delta.name or state["name"]
            state["arguments"] += delta.arguments or ""
    return LLMCompletion(
        content="".join(fragments) or None,
        tool_calls=[
            _tool_call(state["id"], state["name"], state["arguments"])
            for _, state in sorted(call_state.items())
        ],
        usage=usage,
        model=response_model,
        status=status,
        output_item_types=output_item_types,
        incomplete_reason=incomplete_reason,
    )


__all__ = ["create_structured_completion"]
