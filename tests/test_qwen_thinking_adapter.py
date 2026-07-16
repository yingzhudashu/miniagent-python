"""Explicit provider compatibility adapters replace endpoint heuristics."""

from miniagent.llm.gateway import LLMGateway
from miniagent.llm.types import ModelDescriptor


def _descriptor(compatibility=None) -> ModelDescriptor:
    return ModelDescriptor(
        profile="qwen",
        provider="compatible",
        model="qwen-plus",
        api="openai_chat_completions",
        compatibility=compatibility or {},
    )


def test_explicit_qwen_adapter_enables_thinking() -> None:
    params = LLMGateway._provider_params(
        {"_thinking_level": "high", "_thinking_budget": 2048},
        _descriptor({"thinking_adapter": "qwen"}),
    )
    assert params["extra_body"] == {
        "enable_thinking": True,
        "thinking_budget": 2048,
    }


def test_explicit_qwen_adapter_disables_thinking() -> None:
    params = LLMGateway._provider_params(
        {"_thinking_level": "disabled", "_thinking_budget": 2048},
        _descriptor({"thinking_adapter": "qwen"}),
    )
    assert params["extra_body"] == {"enable_thinking": False}


def test_model_name_alone_never_selects_adapter() -> None:
    params = LLMGateway._provider_params(
        {"_thinking_level": "high", "_thinking_budget": 2048}, _descriptor()
    )
    assert "extra_body" not in params
