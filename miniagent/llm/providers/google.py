"""Optional Google Gemini provider using the google-genai SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from miniagent.llm.providers.common import (
    field,
    normalize_provider_error,
    tool_call,
    usage_from_fields,
)
from miniagent.llm.types import (
    LLMCompletion,
    LLMStreamEvent,
    LLMToolCallDelta,
    ModelDescriptor,
)


def _google_parts(content: Any) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if not isinstance(content, list):
        return parts
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            parts.append({"text": str(part.get("text") or "")})
        elif part.get("type") in ("image", "image_url"):
            source = part.get("source") or {}
            data = part.get("data") or source.get("data")
            mime = part.get("mime_type") or source.get("media_type") or "image/png"
            if data:
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
    return parts


def _google_tool_call_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            import json

            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        parts.append(
            {
                "function_call": {
                    "name": str(function.get("name") or ""),
                    "args": arguments,
                }
            }
        )
    return parts


def _google_contents(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content")
        if role in ("system", "developer"):
            if isinstance(content, str):
                system_parts.append(content)
            continue
        if role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": str(message.get("name") or "tool"),
                                "response": {"result": content},
                            }
                        }
                    ],
                }
            )
            continue
        if role not in ("user", "assistant", "model"):
            continue
        parts = _google_parts(content)
        if role == "assistant" and message.get("tool_calls"):
            parts.extend(_google_tool_call_parts(message))
        contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return ("\n\n".join(system_parts) or None), contents


def _google_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    declarations = []
    for tool in tools or []:
        function = tool.get("function") if tool.get("type") == "function" else tool
        if isinstance(function, dict):
            declarations.append(
                {
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
    return [{"function_declarations": declarations}] if declarations else []


class GoogleProvider:
    """Gemini adapter loaded only when the optional google-genai SDK exists."""

    def __init__(
        self,
        provider_id: str,
        *,
        api_key: str,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        try:
            from google import genai
        except ImportError as error:
            raise RuntimeError(
                "Google provider requires: pip install 'miniagent-python[providers]'"
            ) from error
        http_options: dict[str, Any] = {"timeout": int(timeout * 1_000)}
        if base_url:
            http_options["base_url"] = base_url
        if headers:
            http_options["headers"] = dict(headers)
        self._provider_id = provider_id
        self._client = genai.Client(api_key=api_key, http_options=http_options)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    async def list_models(self) -> Sequence[ModelDescriptor]:
        pager = await self._client.aio.models.list()
        result = []
        async for item in pager if hasattr(pager, "__aiter__") else _empty_async():
            model_id = str(field(item, "name", "") or "").removeprefix("models/")
            if model_id:
                result.append(
                    ModelDescriptor(
                        profile=f"{self.provider_id}:{model_id}",
                        provider=self.provider_id,
                        model=model_id,
                        api="google_generate_content",
                    )
                )
        if not result and not hasattr(pager, "__aiter__"):
            for item in pager or ():
                model_id = str(field(item, "name", "") or "").removeprefix("models/")
                if model_id:
                    result.append(
                        ModelDescriptor(
                            profile=f"{self.provider_id}:{model_id}",
                            provider=self.provider_id,
                            model=model_id,
                            api="google_generate_content",
                        )
                    )
        return tuple(result)

    def _request(
        self,
        model: ModelDescriptor,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None,
        json_mode: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        system, contents = _google_contents(messages)
        config: dict[str, Any] = {
            "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "max_output_tokens": int(params.get("max_tokens", model.max_output_tokens)),
        }
        if system:
            config["system_instruction"] = system
        converted_tools = _google_tools(tools)
        if converted_tools:
            config["tools"] = converted_tools
        if json_mode:
            config["response_mime_type"] = "application/json"
        return contents, {key: value for key, value in config.items() if value is not None}

    @staticmethod
    def _parts(response: Any) -> list[Any]:
        candidates = field(response, "candidates", ()) or ()
        if not candidates:
            return []
        return list(field(field(candidates[0], "content"), "parts", ()) or ())

    @staticmethod
    def _usage(response: Any) -> Any:
        usage = field(response, "usage_metadata")
        return usage_from_fields(
            input_tokens=field(usage, "prompt_token_count", 0),
            output_tokens=field(usage, "candidates_token_count", 0),
            reasoning_tokens=field(usage, "thoughts_token_count", 0),
            cache_read_tokens=field(usage, "cached_content_token_count", 0),
        )

    async def create_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        contents, config = self._request(model, messages, params, tools, json_mode)
        try:
            response = await self._client.aio.models.generate_content(
                model=model.model, contents=contents, config=config
            )
        except Exception as error:
            raise normalize_provider_error(error, self.provider_id) from None
        text_parts = []
        calls = []
        for part in self._parts(response):
            text = field(part, "text")
            call = field(part, "function_call")
            if text:
                text_parts.append(str(text))
            if call:
                calls.append(tool_call("", field(call, "name"), field(call, "args")))
        return LLMCompletion(
            content="".join(text_parts) or None,
            tool_calls=calls,
            usage=self._usage(response),
            model=model.model,
            finish_reason=str(
                field(field(response, "candidates", [None])[0], "finish_reason", "") or ""
            )
            or None,
        )

    async def stream_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        contents, config = self._request(model, messages, params, tools, json_mode)
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=model.model, contents=contents, config=config
            )
            call_index = 0
            async for chunk in stream:
                for part in self._parts(chunk):
                    text = field(part, "text")
                    call = field(part, "function_call")
                    if text:
                        event_type = "thinking_delta" if field(part, "thought", False) else "text_delta"
                        yield LLMStreamEvent(
                            event_type=event_type,
                            thinking_delta=str(text) if event_type == "thinking_delta" else None,
                            content_delta=str(text) if event_type == "text_delta" else None,
                        )
                    if call:
                        import json

                        yield LLMStreamEvent(
                            event_type="tool_call_delta",
                            tool_call_delta=LLMToolCallDelta(
                                index=call_index,
                                name=str(field(call, "name", "") or ""),
                                arguments=json.dumps(
                                    field(call, "args", {}) or {}, ensure_ascii=False
                                ),
                            ),
                        )
                        call_index += 1
                usage = self._usage(chunk)
                if usage.total_tokens:
                    yield LLMStreamEvent(usage=usage)
            yield LLMStreamEvent(event_type="completed", completed=True, status="completed")
        except Exception as error:
            raise normalize_provider_error(error, self.provider_id) from None

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


async def _empty_async():
    if False:
        yield None


__all__ = ["GoogleProvider"]
