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
from typing import TYPE_CHECKING, Any

from miniagent.core._openai_compat import (
    ensure_json_object_user_message,
    json_object_unsupported,
)
from miniagent.core.llm_transport import (
    LLMCompletion,
    classify_transport_error,
    completion_failure_category,
    create_completion,
    create_structured_completion,
    resolve_wire_api,
    structured_retry_delay,
    structured_retry_params,
)
from miniagent.infrastructure.json_config import get_config

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openai import AsyncOpenAI


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
    client: AsyncOpenAI,
    model: str | None = None,
    raise_on_error: bool = False,
    *,
    max_tokens: int | None = None,
    thinking_level: str | None = None,
    thinking_budget: int | None = None,
    model_overrides: dict[str, Any] | None = None,
    trace_phase: str | None = None,
    trace_session_key: str | None = None,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 回复。

    响应文本统一经 :func:`parse_llm_json_response` 解析，因此 ``json_object`` 降级路径
    同样享有围栏剥离与大括号截取等容错。Responses 从首次请求起使用流式聚合，
    空文本、解析失败和瞬时网关错误最多尝试三次；Chat 保持两次非流式尝试。
    确定性的 API 错误不会由本层额外重试。

    Args:
        prompt: 用户提示
        system: 系统提示
        client: LLM 客户端（None 时回落到共享工厂）
        model: 模型名（None 时读取 ``MINIAGENT_MODEL_MODEL`` 环境变量，回落到 ``gpt-4o-mini``）
        raise_on_error: 解析失败时是否抛出异常（默认 False，返回空字典）
        max_tokens: 可选输出 token 上限。结构化 JSON 评估/分类类调用通常不需要大输出，
            限制后可显著降低延迟（实测 reflect -34%）。None 时不传，沿用服务端默认。
        thinking_level: 可选思考档位（如 ``"low"`` / ``"disabled"``）。JSON 评分类任务无需深度思考；
            提供时经 ``build_thinking_extra_body`` 注入 ``extra_body``，对不支持的端点自动无副作用。
        thinking_budget: 与 ``thinking_level`` 配套的思考预算（token）。
        model_overrides: 可选 ``AgentConfig.model_overrides``；未提供时回落到默认 Agent 配置，
            以便 ``extra_body`` 等用户自定义字段在 JSON 调用路径生效。
        trace_phase: 可选 trace 阶段标签（如 ``"reflect"`` / ``"clarify"``）；提供时发出
            ``llm.request`` / ``llm.response`` 事件，消除该阶段的 trace 盲区。
        trace_session_key: trace 事件归属的会话标识。

    Returns:
        解析后的 JSON 字典；解析失败时：
        - raise_on_error=False：返回空字典 {}
        - raise_on_error=True：抛出 :exc:`json.JSONDecodeError` 或 :exc:`TypeError`

    Raises:
        json.JSONDecodeError: 当 raise_on_error=True 且文本无法解析时
        TypeError: 当 raise_on_error=True 且解析结果不是 JSON 对象时
        Exception: API 调用失败且非 ``json_object`` 不支持类错误时原样抛出

    Note:
        OpenAI API 要求：使用 response_format=json_object 时，
        消息中必须包含 "json" 这个词（不区分大小写）。
        本函数会自动检查并添加必要的提示；若端点不支持 json_object，
        会降级为普通请求，再经 :func:`parse_llm_json_response` 容错解析。
    """
    llm = client
    if model is None:
        model = get_config("model.model", "gpt-4o-mini")

    base_messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    json_object_messages = ensure_json_object_user_message(base_messages)

    create_kwargs: dict[str, Any] = {"model": model}
    if max_tokens is not None:
        create_kwargs["max_tokens"] = int(max_tokens)
    if thinking_level is not None:
        from miniagent.core.config import get_default_agent_config
        from miniagent.core.vendor.qwen_extra import build_thinking_extra_body

        base_url = get_config("model.base_url", None)
        overrides = (
            dict(model_overrides)
            if model_overrides is not None
            else dict(get_default_agent_config().model_overrides)
        )
        extra = build_thinking_extra_body(
            base_url,
            thinking_level,
            int(thinking_budget or 0),
            model_overrides_extra=overrides,
        )
        if extra:
            create_kwargs["extra_body"] = extra
        create_kwargs["_thinking_level"] = thinking_level
        create_kwargs["_thinking_budget"] = int(thinking_budget or 0)

    used_json_object = True
    resp: LLMCompletion | None = None
    from miniagent.core.config import get_default_model_config

    model_config = get_default_model_config()
    responses_wire = resolve_wire_api() == "responses"
    max_attempts = 3 if responses_wire else 2
    attempt_kwargs = dict(create_kwargs)

    def _emit_llm_trace(
        event_type: str,
        *,
        json_object: bool,
        attempt: int,
        failure_category: str | None = None,
        retrying: bool = False,
        duration_ms: int | None = None,
    ) -> None:
        if not trace_phase:
            return
        from miniagent.infrastructure.tracing import emit_trace

        payload: dict[str, Any] = {
            "type": event_type,
            "phase": trace_phase,
            "session_key": trace_session_key or "default",
            "model": model,
            "json_object": json_object,
            "attempt": attempt,
            "structured_stream": responses_wire and json_object,
            "failure_category": failure_category,
            "retrying": bool(failure_category) and retrying,
        }
        if event_type == "llm.request":
            payload.update({"message_count": 2, "tool_count": 0})
        if event_type == "llm.response" and duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if event_type == "llm.response" and resp is not None:
            _usage = getattr(resp, "usage", None)
            payload["usage"] = (
                _usage.model_dump()
                if _usage is not None and hasattr(_usage, "model_dump")
                else None
            )
            payload.update(
                {
                    "status": resp.status,
                    "output_item_types": list(resp.output_item_types),
                    "incomplete_reason": resp.incomplete_reason,
                }
            )
        emit_trace(payload)

    for attempt in range(max_attempts):
        attempt_number = attempt + 1
        attempt_start_ns = time.monotonic_ns()
        resp = None
        _emit_llm_trace(
            "llm.request",
            json_object=used_json_object,
            attempt=attempt_number,
        )
        try:
            if used_json_object:
                resp = await create_structured_completion(
                    llm,
                    messages=json_object_messages,
                    params=attempt_kwargs,
                )
            else:
                resp = await create_completion(
                    llm,
                    messages=base_messages,
                    params=attempt_kwargs,
                )
        except Exception as api_err:
            if used_json_object and json_object_unsupported(api_err):
                used_json_object = False
                _logger.info("llm_json: API 不支持 json_object，已降级为普通 JSON 输出")
                _emit_llm_trace(
                    "llm.response",
                    json_object=True,
                    attempt=attempt_number,
                    failure_category="json_object_unsupported",
                    retrying=True,
                    duration_ms=(time.monotonic_ns() - attempt_start_ns) // 1_000_000,
                )
                _emit_llm_trace(
                    "llm.request",
                    json_object=False,
                    attempt=attempt_number,
                )
                fallback_start_ns = time.monotonic_ns()
                try:
                    resp = await create_completion(
                        llm,
                        messages=base_messages,
                        params=attempt_kwargs,
                    )
                except Exception as fallback_error:
                    fallback_failure = classify_transport_error(fallback_error)
                    _emit_llm_trace(
                        "llm.response",
                        json_object=False,
                        attempt=attempt_number,
                        failure_category=fallback_failure.category,
                        retrying=False,
                        duration_ms=(
                            time.monotonic_ns() - fallback_start_ns
                        ) // 1_000_000,
                    )
                    raise
            else:
                failure = classify_transport_error(api_err)
                will_retry = (
                    responses_wire
                    and failure.retryable
                    and attempt < max_attempts - 1
                )
                _emit_llm_trace(
                    "llm.response",
                    json_object=used_json_object,
                    attempt=attempt_number,
                    failure_category=failure.category,
                    retrying=will_retry,
                    duration_ms=(time.monotonic_ns() - attempt_start_ns) // 1_000_000,
                )
                if will_retry:
                    next_attempt = attempt_number + 1
                    _logger.info(
                        "LLM JSON 第 %d 次遇到可恢复的 %s，准备重试",
                        attempt_number,
                        failure.category,
                    )
                    attempt_kwargs = structured_retry_params(
                        attempt_kwargs,
                        next_attempt=next_attempt,
                        max_attempts=max_attempts,
                        final_reasoning="low",
                        model_max_tokens=model_config.max_tokens,
                    )
                    await asyncio.sleep(structured_retry_delay(next_attempt))
                    continue
                raise

        text = (resp.content or "").strip()
        failure_category = completion_failure_category(resp)
        parsed: dict[str, Any] | None = None
        parse_error: json.JSONDecodeError | TypeError | None = None
        try:
            if text:
                parsed = parse_llm_json_response(text)
        except (json.JSONDecodeError, TypeError) as error:
            parse_error = error
            failure_category = "invalid_json"

        _emit_llm_trace(
            "llm.response",
            json_object=used_json_object,
            attempt=attempt_number,
            failure_category=failure_category,
            retrying=bool(failure_category) and attempt < max_attempts - 1,
            duration_ms=(time.monotonic_ns() - attempt_start_ns) // 1_000_000,
        )
        if parsed is not None:
            return parsed

        if attempt < max_attempts - 1:
            next_attempt = attempt_number + 1
            _logger.info(
                "LLM JSON 第 %d 次返回 %s，准备重试",
                attempt_number,
                failure_category or "invalid_json",
            )
            if responses_wire:
                attempt_kwargs = structured_retry_params(
                    attempt_kwargs,
                    next_attempt=next_attempt,
                    max_attempts=max_attempts,
                    final_reasoning="low",
                    model_max_tokens=model_config.max_tokens,
                    incomplete_reason=resp.incomplete_reason,
                )
                await asyncio.sleep(structured_retry_delay(next_attempt))
            continue

        _logger.warning(
            "LLM JSON 最终失败: attempts=%d, category=%s",
            max_attempts,
            failure_category or "invalid_json",
        )
        if raise_on_error:
            if parse_error is not None:
                raise parse_error
            raise ValueError(
                "LLM returned empty text content or no usable structured JSON "
                f"({failure_category or 'empty_gateway_response'})"
            )
        return {}

    assert False, "unreachable"


__all__ = ["llm_json", "parse_llm_json_response"]
