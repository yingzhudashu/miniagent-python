"""Shared LLM JSON helper — 供 control 层模块复用的 JSON 解析工具。

本模块提供以下函数：

1. ``llm_json()`` — 调用 LLM 并解析 JSON 回复（需网络请求）
2. ``parse_llm_json_response()`` — 解析 LLM 返回的 JSON 字符串（围栏剥离、大括号截取）

使用场景：
- ``problem_solver.py`` / ``requirement_clarifier.py`` — ``llm_json()`` 一站式调用
- ``planner.py`` / ``task_classifier.py`` — 自管 LLM 调用后用 ``parse_llm_json_response()``

**注意**：``llm_json()`` 需要网络请求，单元测试中应通过 patch 或注入 Mock client 避免真实调用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from miniagent.llm.gateway import LLMGateway
from miniagent.llm.openai_compat import (
    ensure_json_object_user_message,
    json_object_unsupported,
)
from miniagent.llm.recovery import (
    classify_transport_error,
    completion_failure_category,
    structured_retry_delay,
    structured_retry_params,
)
from miniagent.llm.requests import create_structured_completion
from miniagent.llm.types import LLMCompletion, LLMRole

_logger = logging.getLogger(__name__)

@dataclass
class _JsonTrace:
    """持有一次结构化调用序列的追踪上下文。"""

    phase: str | None
    session_key: str | None
    model: str
    responses_wire: bool
    base_messages: list[dict[str, str]]
    json_messages: list[dict[str, str]]
    call_id: str = ""
    response: LLMCompletion | None = None
    response_emitted: bool = False

    def new_call(self) -> None:
        """为一次物理 API 请求生成新的关联 ID。"""
        from miniagent.agent.observability import new_trace_id

        self.call_id = new_trace_id("llm")
        self.response = None
        self.response_emitted = False

    def emit(
        self,
        event_type: str,
        *,
        json_object: bool,
        attempt: int,
        failure_category: str | None = None,
        retrying: bool = False,
        duration_ms: int | None = None,
    ) -> None:
        """记录安全请求元数据和规范化响应状态。"""
        if not self.phase:
            return
        from miniagent.agent.observability import emit_trace, llm_request_size_metrics

        payload: dict[str, Any] = {
            "type": event_type,
            "call_id": self.call_id,
            "phase": self.phase,
            "session_key": self.session_key or "default",
            "model": self.model,
            "json_object": json_object,
            "attempt": attempt,
            "structured_stream": self.responses_wire and json_object,
            "failure_category": failure_category,
            "retrying": bool(failure_category) and retrying,
        }
        if event_type == "llm.request":
            messages = self.json_messages if json_object else self.base_messages
            payload.update(
                {
                    "message_count": len(messages),
                    "tool_count": 0,
                    **llm_request_size_metrics(messages),
                }
            )
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if event_type == "llm.response" and self.response is not None:
            usage = self.response.usage
            payload.update(
                {
                    "usage": usage.model_dump()
                    if usage is not None and hasattr(usage, "model_dump")
                    else None,
                    "status": self.response.status,
                    "output_item_types": list(self.response.output_item_types),
                    "incomplete_reason": self.response.incomplete_reason,
                }
            )
        emit_trace(payload)
        if event_type == "llm.response":
            self.response_emitted = True


def _build_json_completion_kwargs(
    *,
    max_tokens: int | None,
    thinking_level: str | None,
    thinking_budget: int | None,
    llm_overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """构建 JSON 调用参数，并按需注入供应商思考字段。"""
    kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = int(max_tokens)
    if thinking_level is None:
        return kwargs
    if llm_overrides:
        kwargs.update(llm_overrides)
    kwargs["_thinking_level"] = thinking_level
    kwargs["_thinking_budget"] = int(thinking_budget or 0)
    return kwargs


async def _request_json_once(
    client: LLMGateway,
    *,
    base_messages: list[dict[str, str]],
    json_messages: list[dict[str, str]],
    params: dict[str, Any],
    use_json_object: bool,
    attempt: int,
    trace: _JsonTrace,
    role: LLMRole,
    profile: str | None,
) -> tuple[LLMCompletion, bool, int]:
    """执行一次逻辑请求；不支持 json_object 时同一 attempt 内降级。"""
    trace.new_call()
    start_ns = time.monotonic_ns()
    trace.emit("llm.request", json_object=use_json_object, attempt=attempt)
    try:
        response = (
            await create_structured_completion(
                client,
                role=role,
                profile=profile,
                messages=json_messages,
                params=params,
            )
            if use_json_object
            else await client.create_completion(
                role=role,
                profile=profile,
                messages=base_messages,
                params=params,
            )
        )
        trace.response = response
        return response, use_json_object, start_ns
    except Exception as error:
        if not use_json_object or not json_object_unsupported(error):
            raise
        trace.emit(
            "llm.response",
            json_object=True,
            attempt=attempt,
            failure_category="json_object_unsupported",
            retrying=True,
            duration_ms=(time.monotonic_ns() - start_ns) // 1_000_000,
        )
    _logger.info("llm_json: API 不支持 json_object，已降级为普通 JSON 输出")
    trace.new_call()
    fallback_start_ns = time.monotonic_ns()
    trace.emit("llm.request", json_object=False, attempt=attempt)
    try:
        response = await client.create_completion(
            role=role,
            profile=profile,
            messages=base_messages,
            params=params,
        )
    except Exception as error:
        failure = classify_transport_error(error)
        trace.emit(
            "llm.response",
            json_object=False,
            attempt=attempt,
            failure_category=failure.category,
            duration_ms=(time.monotonic_ns() - fallback_start_ns) // 1_000_000,
        )
        raise
    trace.response = response
    return response, False, fallback_start_ns


def _parse_json_completion(
    response: LLMCompletion,
) -> tuple[dict[str, Any] | None, str | None, json.JSONDecodeError | TypeError | None]:
    """解析响应正文，并返回结果、失败分类和可选解析异常。"""
    text = (response.content or "").strip()
    failure_category = completion_failure_category(response)
    if not text:
        return None, failure_category, None
    try:
        return parse_llm_json_response(text), failure_category, None
    except (json.JSONDecodeError, TypeError) as error:
        return None, "invalid_json", error


@dataclass
class _JsonAttemptState:
    """结构化 JSON 重试过程中可变的请求参数与格式开关。"""

    params: dict[str, Any]
    use_json_object: bool = True


async def _run_json_attempt(
    client: LLMGateway,
    *,
    base_messages: list[dict[str, str]],
    json_messages: list[dict[str, str]],
    state: _JsonAttemptState,
    attempt_number: int,
    max_attempts: int,
    responses_wire: bool,
    model_max_tokens: int,
    trace: _JsonTrace,
    role: LLMRole,
    profile: str | None,
) -> tuple[dict[str, Any] | None, Exception | None, str | None]:
    """执行一次 JSON 请求并更新下一次重试参数。"""
    try:
        response, state.use_json_object, started = await _request_json_once(
            client,
            base_messages=base_messages,
            json_messages=json_messages,
            params=state.params,
            use_json_object=state.use_json_object,
            attempt=attempt_number,
            trace=trace,
            role=role,
            profile=profile,
        )
    except Exception as error:
        failure = classify_transport_error(error)
        retry = responses_wire and failure.retryable and attempt_number < max_attempts
        if not trace.response_emitted:
            trace.emit(
                "llm.response",
                json_object=state.use_json_object,
                attempt=attempt_number,
                failure_category=failure.category,
                retrying=retry,
            )
        if retry:
            next_attempt = attempt_number + 1
            state.params = structured_retry_params(
                state.params,
                next_attempt=next_attempt,
                max_attempts=max_attempts,
                final_reasoning="low",
                model_max_tokens=model_max_tokens,
            )
            await asyncio.sleep(structured_retry_delay(next_attempt))
        return None, error, failure.category
    parsed, category, parse_error = _parse_json_completion(response)
    trace.emit(
        "llm.response",
        json_object=state.use_json_object,
        attempt=attempt_number,
        failure_category=category,
        retrying=bool(category) and attempt_number < max_attempts,
        duration_ms=(time.monotonic_ns() - started) // 1_000_000,
    )
    if parsed is not None or attempt_number >= max_attempts:
        return parsed, parse_error, category
    _logger.info(
        "LLM JSON 第 %d 次返回 %s，准备重试",
        attempt_number,
        category or "invalid_json",
    )
    if responses_wire:
        next_attempt = attempt_number + 1
        state.params = structured_retry_params(
            state.params,
            next_attempt=next_attempt,
            max_attempts=max_attempts,
            final_reasoning="low",
            model_max_tokens=model_max_tokens,
            incomplete_reason=response.incomplete_reason,
        )
        await asyncio.sleep(structured_retry_delay(next_attempt))
    return None, parse_error, category


def _ensure_json_object(parsed: Any) -> dict[str, Any]:
    """Return *parsed* when it is a JSON object mapping; otherwise raise TypeError."""
    if isinstance(parsed, dict):
        return parsed
    raise TypeError(f"Expected JSON object, got {type(parsed).__name__}")


def parse_llm_json_response(content: str, *, strip_fence: bool = True) -> dict[str, Any]:
    """解析 LLM 返回的 JSON 文本，处理常见格式问题。

    处理策略：
    1. 去除 markdown 围栏（```json / ```），**仅当文本以** `` ``` `` **开头时**
    2. 尝试直接解析
    3. 失败时截取首尾大括号 ``{...}`` 内容再次解析（仅适用于顶层 JSON 对象）

    限制：
    - 前置说明文字后的围栏不会自动剥离，但若正文含 ``{...}`` 仍可能经步骤 3 成功
    - 顶层 JSON 数组 ``[...]`` 或标量会触发 :exc:`TypeError`

    Args:
        content: LLM 返回的文本内容
        strip_fence: 是否去除开头的 markdown 围栏（默认 True）

    Returns:
        解析后的 JSON 对象（dict）

    Raises:
        json.JSONDecodeError: 文本无法解析为 JSON
        TypeError: 解析成功但顶层不是 JSON 对象
    """
    text = content.strip()

    # 去除 markdown 围栏
    if strip_fence and text.startswith("```"):
        # 常见围栏格式：```json\n{...}\n``` 或 ```\n{...}\n```
        lines = text.split("\n")
        # 移除首行的 ```json 或 ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除末行的 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 尝试直接解析
    try:
        return _ensure_json_object(json.loads(text))
    except json.JSONDecodeError:
        # 失败时截取首尾大括号
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return _ensure_json_object(json.loads(text[start : end + 1]))
            except json.JSONDecodeError as e:
                _logger.debug("JSON修复失败: %s", e)
            except TypeError:
                raise
        # 无法修复，重新抛出原始异常
        raise


async def llm_json(
    prompt: str,
    system: str,
    client: LLMGateway,
    raise_on_error: bool = False,
    *,
    max_tokens: int | None = None,
    thinking_level: str | None = None,
    thinking_budget: int | None = None,
    llm_overrides: dict[str, Any] | None = None,
    trace_phase: str | None = None,
    trace_session_key: str | None = None,
    role: LLMRole = "reasoning",
    profile: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 对象。

    Responses 对空文本、解析错误和暂态传输错误最多尝试三次，Chat 尝试两次；
    JSON Object 不受支持时同轮降级。最终解析错误默认返回空字典，
    ``raise_on_error=True`` 时抛出原始解析异常。
    """
    base_messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    json_object_messages = ensure_json_object_user_message(base_messages)

    create_kwargs = _build_json_completion_kwargs(
        max_tokens=max_tokens,
        thinking_level=thinking_level,
        thinking_budget=thinking_budget,
        llm_overrides=llm_overrides,
    )

    descriptor = client.catalog.get(profile) if profile else client.model_for_role(role)
    if descriptor is None:
        raise ValueError(f"Unknown model profile: {profile}")
    responses_wire = descriptor.api == "openai_responses"
    max_attempts = 3 if responses_wire else 2
    attempt_state = _JsonAttemptState(dict(create_kwargs))
    trace = _JsonTrace(
        phase=trace_phase,
        session_key=trace_session_key,
        model=descriptor.model,
        responses_wire=responses_wire,
        base_messages=base_messages,
        json_messages=json_object_messages,
    )

    for attempt_number in range(1, max_attempts + 1):
        parsed, error, failure_category = await _run_json_attempt(
            client,
            base_messages=base_messages,
            json_messages=json_object_messages,
            state=attempt_state,
            attempt_number=attempt_number,
            max_attempts=max_attempts,
            responses_wire=responses_wire,
            model_max_tokens=descriptor.max_output_tokens,
            trace=trace,
            role=role,
            profile=profile,
        )
        if parsed is not None:
            return parsed
        if attempt_number < max_attempts:
            parse_error = isinstance(error, (json.JSONDecodeError, TypeError))
            retryable_transport = bool(
                error is not None
                and responses_wire
                and classify_transport_error(error).retryable
            )
            if error is not None and not (parse_error or retryable_transport):
                raise error
            continue
        _logger.warning(
            "LLM JSON 最终失败: attempts=%d, category=%s",
            max_attempts,
            failure_category or "invalid_json",
        )
        if error is not None and not isinstance(error, (json.JSONDecodeError, TypeError)):
            raise error
        if raise_on_error:
            if error is not None:
                raise error
            raise ValueError(
                "LLM returned empty text content or no usable structured JSON "
                f"({failure_category or 'empty_gateway_response'})"
            )
        return {}

    raise AssertionError("unreachable")  # pragma: no cover - defensive guard


__all__ = ["llm_json", "parse_llm_json_response"]
