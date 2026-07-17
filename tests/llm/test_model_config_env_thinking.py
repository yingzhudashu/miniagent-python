"""Thinking compatibility is descriptor-driven, never inferred from endpoint URLs."""

from miniagent.llm.gateway import LLMGateway
from miniagent.llm.types import ModelDescriptor


def test_qwen_thinking_adapter_is_explicit() -> None:
    descriptor = ModelDescriptor(
        profile="qwen",
        provider="openai-compatible",
        model="qwen-plus",
        api="openai_chat_completions",
        compatibility={"thinking_adapter": "qwen"},
    )
    params = LLMGateway._provider_params(
        {"_thinking_level": "medium", "_thinking_budget": 512}, descriptor
    )
    assert params["extra_body"] == {"enable_thinking": True, "thinking_budget": 512}


def test_endpoint_name_does_not_enable_qwen_adapter() -> None:
    descriptor = ModelDescriptor(
        profile="plain",
        provider="openai-compatible",
        model="qwen-plus",
        api="openai_chat_completions",
    )
    params = LLMGateway._provider_params(
        {"_thinking_level": "medium", "_thinking_budget": 512}, descriptor
    )
    assert "extra_body" not in params
