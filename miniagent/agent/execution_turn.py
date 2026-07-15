"""执行阶段单轮流式聚合与思考输出状态机。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from miniagent.agent.activity import invoke_activity_log
from miniagent.agent.constants import (
    EXECUTION_CALLBACK_MIN_CHARS,
    EXECUTION_CALLBACK_MIN_INTERVAL_MS,
)
from miniagent.agent.execution_stream import StreamingBuffer
from miniagent.agent.executor import (
    _EXEC_LLM_MAX_ATTEMPTS,
    _exec_retry_params,
    _extract_tool_intent,
    _logger,
    _raise_if_task_cancelled,
    _tool_intent_in_thinking_enabled,
)
from miniagent.agent.llm_params import resolve_exec_completion_kwargs
from miniagent.agent.logging import append_log, truncate
from miniagent.agent.observability import emit_trace, llm_request_size_metrics, new_trace_id
from miniagent.agent.thinking_callback import invoke_on_thinking
from miniagent.llm.legacy_transport import (
    LLMTransportError,
    classify_transport_error,
    resolve_wire_api,
    stream_completion,
)
from miniagent.llm.message_sanitize import strip_leading_underscore_keys_from_messages


@dataclass
class _AttemptState:
    """保存一次流式请求的增量聚合状态，不跨重试复用。"""

    content: StreamingBuffer = field(default_factory=StreamingBuffer)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    usage: Any = None
    chars_since_callback: int = 0
    last_callback_ms: int = field(default_factory=lambda: time.monotonic_ns() // 1_000_000)


@dataclass(frozen=True)
class _CompletedAttempt:
    """描述成功流式请求的最终结果及追踪元数据。"""

    call_id: str
    attempt: int
    exec_kwargs: dict[str, Any]
    state: _AttemptState
    duration_ms: int


class ExecutionTurnStreamer:
    """持有跨 LLM 执行轮的流式缓冲、序号与累计正文。"""

    def __init__(
        self,
        *,
        context_manager: Any,
        agent_config: Any,
        on_thinking: Any,
        phase_header_sent: set[str],
        model_config: Any,
        session_key: str,
        llm_client: Any,
        exec_hist_segments: dict[str, list[str]],
        activity_log_enabled: bool,
        activity_log: Any,
        separator: str,
    ) -> None:
        self.context_manager = context_manager
        self.agent_config = agent_config
        self.on_thinking = on_thinking
        self._phase_header_sent = phase_header_sent
        self.model_config = model_config
        self.session_key = session_key
        self.llm_client = llm_client
        self._exec_hist_segments = exec_hist_segments
        self.activity_log_enabled = activity_log_enabled
        self.al = activity_log
        self.sep = separator
        self.exec_turn_no = 0

    def joined_phase_cumulative(self, label: str, current_body: str) -> str:
        """将同一 ``label`` 下历史执行轮正文与 ``current_body`` 用分段符拼接，供思考流 cumulative 展示。

        返回完整累积内容（含历史轮），使引擎端 prefix 检测始终生效。
        工具意图行必须用 ``streaming=False``，否则污染 LLM 正文前缀导致 prefix 匹配失效。
        """
        prev = [p for p in self._exec_hist_segments.get(label, []) if (p or "").strip()]
        if not prev:
            return current_body
        return self.sep.join(prev + [current_body])

    async def _start_thinking(self, label: str, *, is_last_step: bool) -> None:
        """尽力发送阶段起始标记；展示失败不能中断 Agent 执行。"""
        if not self.on_thinking or label in self._phase_header_sent:
            return
        try:
            await invoke_on_thinking(
                self.on_thinking,
                f"{label} 开始",
                False,
                label,
                full_record=f"{label} 开始",
                is_last_step=is_last_step,
            )
            self._phase_header_sent.add(label)
        except Exception as error:
            _logger.debug("思考阶段起始状态推送失败（非关键）: %s", error, exc_info=True)

    def _emit_request_trace(
        self,
        *,
        call_id: str,
        attempt: int,
        turn: int,
        exec_kwargs: dict[str, Any],
        messages: list[Any],
        tools: list[Any],
    ) -> None:
        """记录一次请求的安全元数据，不写入消息正文或工具参数值。"""
        emit_trace(
            {
                "type": "llm.request",
                "call_id": call_id,
                "phase": "exec",
                "session_key": self.session_key,
                "turn": turn,
                "attempt": attempt + 1,
                "model": exec_kwargs["model"],
                "message_count": len(messages),
                "tool_count": len(tools),
                **llm_request_size_metrics(messages, tools),
                "reasoning_level": exec_kwargs.get("_thinking_level"),
                "sampling_removed": (
                    "temperature" not in exec_kwargs and "top_p" not in exec_kwargs
                ),
            }
        )

    async def _push_cumulative_thinking(
        self,
        state: _AttemptState,
        label: str,
        *,
        is_last_step: bool,
    ) -> None:
        """达到节流阈值时推送累计正文，并在展示失败时保留待推送计数。"""
        if not self.on_thinking:
            return
        now_ms = time.monotonic_ns() // 1_000_000
        if (
            now_ms - state.last_callback_ms < EXECUTION_CALLBACK_MIN_INTERVAL_MS
            and state.chars_since_callback < EXECUTION_CALLBACK_MIN_CHARS
        ):
            return
        cumulative = self.joined_phase_cumulative(label, state.content.getvalue())
        try:
            await invoke_on_thinking(
                self.on_thinking,
                cumulative,
                True,
                label,
                full_record=cumulative,
                is_last_step=is_last_step,
            )
        except Exception as error:
            _logger.debug("思考正文状态推送失败（非关键）: %s", error, exc_info=True)
            return
        state.last_callback_ms = now_ms
        state.chars_since_callback = 0

    @staticmethod
    def _merge_tool_call_delta(state: _AttemptState, delta: Any) -> None:
        """按流式索引合并工具调用片段，兼容 id/name 延迟到达。"""
        item = state.tool_calls.setdefault(
            delta.index,
            {"id": delta.id, "name": delta.name, "arguments": ""},
        )
        if delta.id:
            item["id"] = delta.id
        if delta.name:
            item["name"] = delta.name
        if delta.arguments:
            item["arguments"] += delta.arguments

    async def _consume_event(
        self,
        state: _AttemptState,
        event: Any,
        label: str,
        *,
        is_last_step: bool,
    ) -> None:
        """将单个规范化传输事件写入本次请求状态。"""
        if event.usage is not None:
            state.usage = event.usage
        if event.content_delta:
            state.content.append(event.content_delta)
            state.chars_since_callback += len(event.content_delta)
            await self._push_cumulative_thinking(state, label, is_last_step=is_last_step)
        if event.tool_call_delta is not None:
            self._merge_tool_call_delta(state, event.tool_call_delta)

    async def _stream_attempt(
        self,
        *,
        state: _AttemptState,
        messages: list[Any],
        tools: list[Any],
        exec_kwargs: dict[str, Any],
        label: str,
        is_last_step: bool,
    ) -> None:
        """执行一次网络流读取；异常原样交由重试策略分类。"""
        async for event in stream_completion(
            self.llm_client,
            messages=messages,
            tools=tools if tools else None,
            params=exec_kwargs,
        ):
            await self._consume_event(state, event, label, is_last_step=is_last_step)

    def _emit_failure_trace(
        self,
        *,
        call_id: str,
        attempt: int,
        turn: int,
        category: str,
        retrying: bool,
        request_start_ns: int,
    ) -> None:
        """统一记录请求失败或空响应，确保所有重试均可关联。"""
        emit_trace(
            {
                "type": "llm.response",
                "call_id": call_id,
                "phase": "exec",
                "session_key": self.session_key,
                "turn": turn,
                "attempt": attempt + 1,
                "failure_category": category,
                "retrying": retrying,
                "duration_ms": (time.monotonic_ns() - request_start_ns) // 1_000_000,
            }
        )

    def _should_retry_error(
        self,
        error: Exception,
        *,
        state: _AttemptState,
        responses_wire: bool,
        attempt: int,
        call_id: str,
        turn: int,
        request_start_ns: int,
    ) -> bool:
        """应用“产生任何输出后不得自动重放”的传输重试不变量。"""
        has_partial_output = bool(state.content.getvalue() or state.tool_calls)
        failure = classify_transport_error(error)
        retrying = (
            responses_wire
            and not has_partial_output
            and failure.retryable
            and attempt < _EXEC_LLM_MAX_ATTEMPTS - 1
        )
        self._emit_failure_trace(
            call_id=call_id,
            attempt=attempt,
            turn=turn,
            category=failure.category,
            retrying=retrying,
            request_start_ns=request_start_ns,
        )
        if retrying:
            return True
        if responses_wire and not has_partial_output and failure.retryable:
            raise LLMTransportError(
                "LLM endpoint repeatedly rejected the execution request "
                "with a transient gateway error."
            ) from None
        raise error

    def _should_retry_empty(
        self,
        *,
        attempt: int,
        call_id: str,
        turn: int,
        request_start_ns: int,
    ) -> bool:
        """处理 Responses 成功但无正文和工具调用的协议级空响应。"""
        retrying = attempt < _EXEC_LLM_MAX_ATTEMPTS - 1
        self._emit_failure_trace(
            call_id=call_id,
            attempt=attempt,
            turn=turn,
            category="empty_response",
            retrying=retrying,
            request_start_ns=request_start_ns,
        )
        if retrying:
            return True
        raise LLMTransportError(
            "Responses execution returned no text or tool calls after "
            f"{_EXEC_LLM_MAX_ATTEMPTS} attempts."
        )

    async def _request_until_complete(
        self,
        *,
        base_exec_kwargs: dict[str, Any],
        responses_wire: bool,
        messages: list[Any],
        tools: list[Any],
        label: str,
        turn: int,
        is_last_step: bool,
    ) -> _CompletedAttempt:
        """执行有界重试，成功时返回唯一一次可提交的聚合结果。"""
        for attempt in range(_EXEC_LLM_MAX_ATTEMPTS):
            call_id = new_trace_id("llm")
            request_start_ns = time.monotonic_ns()
            state = _AttemptState()
            exec_kwargs = _exec_retry_params(
                base_exec_kwargs,
                attempt=attempt,
                responses=responses_wire,
            )
            self._emit_request_trace(
                call_id=call_id,
                attempt=attempt,
                turn=turn,
                exec_kwargs=exec_kwargs,
                messages=messages,
                tools=tools,
            )
            try:
                await self._stream_attempt(
                    state=state,
                    messages=messages,
                    tools=tools,
                    exec_kwargs=exec_kwargs,
                    label=label,
                    is_last_step=is_last_step,
                )
            except Exception as error:
                if self._should_retry_error(
                    error,
                    state=state,
                    responses_wire=responses_wire,
                    attempt=attempt,
                    call_id=call_id,
                    turn=turn,
                    request_start_ns=request_start_ns,
                ):
                    _logger.info(
                        "Execution turn %d attempt %d hit a retryable gateway error; retrying",
                        turn,
                        attempt + 1,
                    )
                    continue
            content = state.content.getvalue()
            duration_ms = (time.monotonic_ns() - request_start_ns) // 1_000_000
            if content.strip() or state.tool_calls or not responses_wire:
                return _CompletedAttempt(call_id, attempt, exec_kwargs, state, duration_ms)
            if self._should_retry_empty(
                attempt=attempt,
                call_id=call_id,
                turn=turn,
                request_start_ns=request_start_ns,
            ):
                _logger.info(
                    "Execution turn %d attempt %d returned no text or tool calls; retrying",
                    turn,
                    attempt + 1,
                )
        raise AssertionError("unreachable execution retry loop")

    async def _flush_final_thinking(
        self,
        state: _AttemptState,
        label: str,
        *,
        is_last_step: bool,
    ) -> None:
        """发送节流后尚未展示的最终正文。"""
        content = state.content.getvalue()
        if not self.on_thinking or not content or state.chars_since_callback <= 0:
            return
        cumulative = self.joined_phase_cumulative(label, content)
        try:
            await invoke_on_thinking(
                self.on_thinking,
                cumulative,
                True,
                label,
                full_record=cumulative,
                is_last_step=is_last_step,
            )
        except Exception as error:
            _logger.debug("最终思考正文推送失败（非关键）: %s", error, exc_info=True)

    @staticmethod
    def _build_tool_calls(accumulated: dict[int, dict[str, Any]]) -> list[Any]:
        """将传输层工具片段转换为后续执行器使用的兼容消息对象。"""
        result: list[Any] = []
        for index in sorted(accumulated):
            item = accumulated[index]
            try:
                args_dict = json.loads(item["arguments"])
            except (json.JSONDecodeError, TypeError):
                args_dict = {}
            function = SimpleNamespace(name=item["name"], arguments=item["arguments"])
            tool_call = SimpleNamespace(id=item["id"], function=function)
            tool_call._args_dict = args_dict
            result.append(tool_call)
        return result

    async def _emit_tool_intents(self, tool_calls: list[Any], label: str) -> None:
        """尽力展示工具意图；该辅助信息不得改变工具实际执行结果。"""
        if not self.on_thinking or not tool_calls or not _tool_intent_in_thinking_enabled():
            return
        try:
            for tool_call in tool_calls:
                intent = _extract_tool_intent(tool_call.function.name, tool_call._args_dict)
                line = f"🔧 {tool_call.function.name} — {intent}"
                await invoke_on_thinking(
                    self.on_thinking,
                    line,
                    False,
                    label,
                    full_record=line,
                )
        except (json.JSONDecodeError, TypeError):
            await invoke_on_thinking(
                self.on_thinking,
                "🔧 执行操作",
                False,
                label,
                full_record="🔧 执行操作",
            )
        except Exception as error:
            _logger.debug("工具意图状态推送失败（非关键）: %s", error, exc_info=True)

    async def _record_result(
        self,
        *,
        completed: _CompletedAttempt,
        messages: list[Any],
        tools: list[Any],
        tool_calls: list[Any],
        content: str,
        turn: int,
    ) -> None:
        """写入成功追踪、可选调试日志与活动日志。"""
        usage = completed.state.usage
        usage_dict = usage.model_dump() if usage else None
        emit_trace(
            {
                "type": "llm.response",
                "call_id": completed.call_id,
                "phase": "exec",
                "session_key": self.session_key,
                "turn": turn,
                "attempt": completed.attempt + 1,
                "model": completed.exec_kwargs["model"],
                "has_tool_calls": bool(tool_calls),
                "duration_ms": completed.duration_ms,
                "usage": usage_dict,
            }
        )
        if self.agent_config.log_file:
            await asyncio.to_thread(
                append_log,
                self.agent_config.log_file,
                {
                    "phase": "exec",
                    "turn": turn,
                    "req": {
                        "model": completed.exec_kwargs["model"],
                        "messageCount": len(messages),
                        "toolCount": len(tools),
                    },
                    "res": {
                        "hasToolCalls": bool(tool_calls),
                        "toolCalls": [
                            {
                                "name": tool_call.function.name,
                                "args": truncate(tool_call.function.arguments, 300),
                            }
                            for tool_call in tool_calls
                        ],
                        "content": truncate(content, 1000) if content else None,
                        "usage": usage_dict,
                    },
                },
            )
        if self.activity_log_enabled:
            await invoke_activity_log(
                self.al,
                "log_llm_call",
                session_key=self.session_key,
                turn=turn,
                model=completed.exec_kwargs["model"],
                message_count=len(messages),
                tool_count=len(tools),
                thinking=content,
                token_usage=usage_dict if self.agent_config.log_token_usage else None,
            )

    async def stream_exec_turn(
        self,
        merge_overrides: dict[str, Any] | None,
        tools_arg: list[Any],
        thinking_phase_label: str,
        is_last_step: bool = False,
    ) -> tuple[Any, dict[str, Any], int, Any, str, str]:
        """流式调用执行阶段 LLM 一轮，聚合正文与 tool_calls，并驱动 ``self.on_thinking``。

        Args:
            merge_overrides: 模型参数覆盖（如 thinking_level/budget）
            tools_arg: 本轮可用的工具定义列表（传给 LLM tools 参数）
            thinking_phase_label: 思考流分段标题（如 "[执行]" 或 "[步骤 1/3]"）
            is_last_step: 是否为规划的最后一步（最后一步的 LLM 正文不在思考区显示）

        Returns:
            tuple: (msg, usage, elapsed_ms, tool_calls, full_content, thinking_header)
                - msg: LLM 返回的 assistant 消息对象
                - usage: token 用量统计（prompt/completion/total）
                - elapsed_ms: 本轮调用耗时（毫秒）
                - tool_calls: 解析后的 tool_calls 列表（无则空）
                - full_content: 聚合后的正文内容
                - thinking_header: 当前思考分段标题（供工具回调）
        """
        _raise_if_task_cancelled()
        self.exec_turn_no += 1
        start_ms = time.monotonic_ns() // 1_000_000
        messages = strip_leading_underscore_keys_from_messages(
            list(self.context_manager.get_messages())
        )
        turn_display = self.exec_turn_no

        if self.agent_config.debug:
            _logger.debug(
                "LLM 请求 (第 %d 轮): 消息数=%d, 工具数=%d",
                turn_display,
                len(messages),
                len(tools_arg),
            )

        await self._start_thinking(thinking_phase_label, is_last_step=is_last_step)
        base_exec_kw = resolve_exec_completion_kwargs(
            self.agent_config, stream=True, merge_overrides=merge_overrides
        )
        if getattr(type(self.llm_client), "_miniagent_llm_gateway", False) is True:
            responses_wire = (
                resolve_wire_api(
                    client=self.llm_client,
                    role=str(base_exec_kw.get("_role") or "default"),
                )
                == "responses"
            )
        else:
            responses_wire = self.model_config.wire_api == "responses"
        completed = await self._request_until_complete(
            base_exec_kwargs=base_exec_kw,
            responses_wire=responses_wire,
            messages=messages,
            tools=tools_arg,
            label=thinking_phase_label,
            turn=turn_display,
            is_last_step=is_last_step,
        )
        full_content = completed.state.content.getvalue()
        await self._flush_final_thinking(
            completed.state,
            thinking_phase_label,
            is_last_step=is_last_step,
        )
        full_tool_calls = self._build_tool_calls(completed.state.tool_calls)
        msg = SimpleNamespace(
            content=full_content or None,
            tool_calls=full_tool_calls or None,
        )
        await self._emit_tool_intents(full_tool_calls, thinking_phase_label)
        await self._record_result(
            completed=completed,
            messages=messages,
            tools=tools_arg,
            tool_calls=full_tool_calls,
            content=full_content,
            turn=turn_display,
        )
        if (full_content or "").strip():
            self._exec_hist_segments.setdefault(thinking_phase_label, []).append(full_content)
        return (
            msg,
            completed.exec_kwargs,
            start_ms,
            completed.state.usage,
            full_content,
            thinking_phase_label,
        )


__all__ = ["ExecutionTurnStreamer"]
