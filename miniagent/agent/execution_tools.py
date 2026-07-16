"""工具调用阶段的循环检测、并发执行与结果回注。"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any

from miniagent.agent.constants import MAX_ARGS_LOG_LEN
from miniagent.agent.executor import (
    _append_context_or_return,
    _extract_tool_intent,
    _logger,
    _raise_if_task_cancelled,
)
from miniagent.agent.observability import emit_trace
from miniagent.agent.ports.runtime import OnThinkingCallback
from miniagent.agent.thinking_callback import invoke_on_thinking
from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.agent.types.errors import (
    FeishuConfigMissingError,
    LarkOapiMissingError,
    SandboxViolationError,
)
from miniagent.agent.types.tool import ToolPermission, ToolResult


async def _await_tool_confirmation(
    *,
    tool_name: str,
    help_text: str,
    args: dict[str, Any],
    permission: ToolPermission,
    confirmation_channel: Any | None,
    agent_config: AgentConfig,
    on_thinking: OnThinkingCallback | None,
    thinking_header: str,
) -> ToolResult | None:
    """``require-confirm`` 工具执行前的用户确认 gate。

    Returns:
        None 表示已确认、无需确认或 ``auto_execute_confirmed`` 跳过；
        ToolResult 表示应直接返回给 LLM（拒绝或未配置通道）。
    """
    if permission != "require-confirm":
        return None
    if agent_config.auto_execute_confirmed:
        return None
    if confirmation_channel is None:
        return ToolResult(
            success=False,
            content=(
                f"{WARNING_PREFIX} 工具 `{tool_name}` 需要用户确认后才能执行，"
                "但当前未配置 confirmation_channel。"
            ),
            meta={"error_type": "ConfirmationRequired"},
        )

    from miniagent.agent.types.confirmation import ConfirmationRequest, ConfirmationStage

    args_preview = json.dumps(args, ensure_ascii=False, indent=2)
    if len(args_preview) > 500:
        args_preview = args_preview[:500] + "…"
    prompt = (
        f"即将执行需确认的工具 `{tool_name}`。\n"
        f"{help_text}\n\n参数:\n{args_preview}\n\n"
        "输入 /confirm 同意，/reject 拒绝。"
    )
    if on_thinking:
        await invoke_on_thinking(
            on_thinking,
            prompt,
            True,
            f"{thinking_header} · 工具确认",
        )
    req = ConfirmationRequest(
        stage=ConfirmationStage.TOOL,
        content=prompt,
        full_content=args_preview,
        context={"tool_name": tool_name, "args": args},
    )
    confirm_result = await confirmation_channel.request_confirmation(req)
    if confirm_result.rejected or not confirm_result.approved:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} 用户拒绝执行工具 `{tool_name}`。",
            meta={"error_type": "ConfirmationRejected"},
        )
    return None


def _truncate_args_for_log(args: dict[str, Any] | str, max_len: int = MAX_ARGS_LOG_LEN) -> str:
    """截断工具参数用于日志输出，避免大内容导致日志膨胀。

    Args:
        args: 工具参数字典或 JSON 字符串
        max_len: 最大长度（字符）

    Returns:
        截断后的字符串
    """
    if isinstance(args, str):
        if len(args) <= max_len:
            return args
        return args[:max_len] + "...[截断]"
    try:
        result = json.dumps(args, ensure_ascii=False)
        if len(result) <= max_len:
            return result
        return result[:max_len] + "...[截断]"
    except Exception:
        return str(args)[:max_len]


def _log_tool_error(
    *,
    tool_name: str,
    tool_call_id: str | None,
    args: dict[str, Any],
    session_key: str | None,
    error_type: str,
    error_message: str,
    is_user_error: bool = False,
    traceback_str: str | None = None,
) -> None:
    """统一记录工具错误日志，区分用户误用与工具缺陷。

    对工具执行错误进行分类记录和 trace 发射，帮助诊断问题根源：
    - 用户误用：权限错误、文件不存在、参数错误等 → WARNING 级别
    - 工具缺陷：内部错误、未捕获异常等 → ERROR 级别，附带堆栈

    Args:
        tool_name: 工具名称
        tool_call_id: LLM 生成的 tool_call ID（可为 None）
        args: 工具参数字典（会被截断以避免日志膨胀）
        session_key: 会话标识符（用于关联会话日志）
        error_type: 异常类型名称（如 "PermissionError"）
        error_message: 错误消息文本
        is_user_error: 是否为用户误用（True=WARNING，False=ERROR）
        traceback_str: 完整堆栈信息（仅非用户错误时记录）

    Note:
        所有错误都会发射 tool.error trace 事件，供监控系统收集。
    """
    args_str = _truncate_args_for_log(args)
    log_prefix = f"[工具错误] {tool_name}"
    emit_trace(
        {
            "type": "tool.error",
            "tool": tool_name,
            "tool_call_id": tool_call_id,
            "args_truncated": args_str,
            "session_key": session_key,
            "error_type": error_type,
            "error_message": error_message,
            "is_user_error": is_user_error,
        }
    )
    if is_user_error:
        _logger.warning(
            "%s | 类型: %s | 参数: %s | 消息: %s | 会话: %s",
            log_prefix,
            error_type,
            args_str,
            error_message,
            session_key or "N/A",
        )
    else:
        _logger.error(
            "%s | 类型: %s | 参数: %s | 消息: %s | 会话: %s",
            log_prefix,
            error_type,
            args_str,
            error_message,
            session_key or "N/A",
        )
        if traceback_str:
            _logger.debug("%s | 堆栈:\n%s", log_prefix, traceback_str)


class ToolPhaseRunner:
    """持有单次计划执行期间的工具并发、确认与循环检测状态。"""

    def __init__(
        self,
        *,
        context_manager: Any,
        agent_config: Any,
        effective_registry: Any,
        session_key: str,
        on_tool_call: Any,
        loop_detector: Any,
        monitor: Any,
        turn_tool_calls: list[Any],
        activity_log_enabled: bool,
        activity_log: Any,
        confirmation_channel: Any,
        on_thinking: Any,
        tool_context: Any,
        execution_semaphore: asyncio.Semaphore,
        on_tool_finish: Any,
        loop_warning_shown: bool = False,
    ) -> None:
        self.context_manager = context_manager
        self.agent_config = agent_config
        self.effective_registry = effective_registry
        self.session_key = session_key
        self.on_tool_call = on_tool_call
        self.loop_detector = loop_detector
        self.monitor = monitor
        self.turn_tool_calls = turn_tool_calls
        self.activity_log_enabled = activity_log_enabled
        self.al = activity_log
        self.confirmation_channel = confirmation_channel
        self.on_thinking = on_thinking
        self.ctx = tool_context
        self.execution_semaphore = execution_semaphore
        self.on_tool_finish = on_tool_finish
        self.loop_warning_shown = loop_warning_shown

    async def invoke_on_tool_finish(
        self,
        name: str,
        args_json: str,
        result: str,
        success: bool,
        thinking_header: str,
    ) -> None:
        """调用 ``self.on_tool_finish`` 回调。"""
        if self.on_tool_finish is None:
            return
        try:
            await self.on_tool_finish(
                name, args_json, result, success, thinking_header=thinking_header
            )
        except Exception as e:
            if self.agent_config.debug:
                _logger.exception("self.on_tool_finish 回调失败: %s", e)

    def _append_assistant_tool_message(self, msg: Any) -> str | None:
        """把 assistant 正文与工具声明写入上下文。"""
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in msg.tool_calls
            ]
        return _append_context_or_return(self.context_manager, assistant_msg)

    async def _handle_unknown_tool(self, tool_call: Any, thinking_header: str) -> str | None:
        """记录模型幻觉工具并向上下文写入可恢复错误。"""
        available = ", ".join(self.effective_registry.list())
        try:
            args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
        except json.JSONDecodeError:
            args = {"raw": tool_call.function.arguments}
        content = f"错误：未知工具 {tool_call.function.name}。可用: {available}"
        _log_tool_error(
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
            args=args,
            session_key=self.session_key,
            error_type="UnknownTool",
            error_message=f"工具不存在，可用工具: {available[:100]}",
            is_user_error=False,
        )
        out_of_budget = _append_context_or_return(
            self.context_manager,
            {"role": "tool", "tool_call_id": tool_call.id, "content": content},
        )
        if self.on_tool_call:
            self.on_tool_call(
                tool_call.function.name,
                tool_call.function.arguments,
                f"{WARNING_PREFIX} 未知工具",
            )
        await self.invoke_on_tool_finish(
            tool_call.function.name,
            tool_call.function.arguments,
            content,
            False,
            thinking_header,
        )
        return out_of_budget

    def _prepare_known_tool(
        self, tool_call: Any, tool: Any, start_ms: int
    ) -> tuple[Any, dict[str, Any], Any] | str:
        """解析工具参数并执行循环检测；严重循环返回终止文本。"""
        try:
            args = getattr(tool_call, "_args_dict", None) or json.loads(
                tool_call.function.arguments
            )
            loop_check = self.loop_detector.check(tool_call.function.name, args)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return tool_call, {}, tool
        if loop_check.level == "critical":
            elapsed = time.monotonic_ns() // 1_000_000 - start_ms
            self.monitor.record(
                tool_call.function.name,
                elapsed,
                False,
                error=loop_check.message,
            )
            _logger.warning("循环检测拦截: %s", loop_check.message)
            return f"{WARNING_PREFIX} 任务执行被终止：{loop_check.message}\n\n建议：简化请求或明确具体目标。"
        if loop_check.level == "warning" and not self.loop_warning_shown:
            self.loop_warning_shown = True
            _logger.warning(loop_check.message)
        return tool_call, args, tool

    async def _collect_pending_tools(
        self,
        tool_calls: list[Any],
        *,
        start_ms: int,
        thinking_header: str,
    ) -> tuple[list[tuple[Any, dict[str, Any], Any]], str | None]:
        """过滤未知工具和严重循环，返回可执行调用列表。"""
        pending: list[tuple[Any, dict[str, Any], Any]] = []
        for tool_call in tool_calls:
            tool = self.effective_registry.get(tool_call.function.name)
            if tool is None:
                error = await self._handle_unknown_tool(tool_call, thinking_header)
                if error:
                    return pending, error
                continue
            prepared = self._prepare_known_tool(tool_call, tool, start_ms)
            if isinstance(prepared, str):
                return pending, prepared
            pending.append(prepared)
        return pending, None

    async def _execute_tool(
        self,
        tool_call: Any,
        args: dict[str, Any],
        tool: Any,
        *,
        timeout_sec: int,
        thinking_header: str,
    ) -> tuple[Any, dict[str, Any], Any, Any, int]:
        """执行单个工具并映射超时、权限、文件和内部异常。"""
        async with self.execution_semaphore:
            _raise_if_task_cancelled()
            start_ms = time.monotonic_ns() // 1_000_000
            cpu_start_ns = time.process_time_ns()
            emit_trace(
                {
                    "type": "tool.start",
                    "session_key": self.session_key,
                    "tool": tool_call.function.name,
                    "tool_call_id": tool_call.id,
                }
            )
            denied = await _await_tool_confirmation(
                tool_name=tool_call.function.name,
                help_text=getattr(tool, "help_text", "") or "",
                args=args,
                permission=getattr(tool, "permission", "sandbox"),
                confirmation_channel=self.confirmation_channel,
                agent_config=self.agent_config,
                on_thinking=self.on_thinking,
                thinking_header=thinking_header,
            )
            if denied is not None:
                elapsed = time.monotonic_ns() // 1_000_000 - start_ms
                return tool_call, args, tool, denied, elapsed
            result = await self._invoke_tool_handler(
                tool_call,
                args,
                tool,
                timeout_sec=timeout_sec,
            )
            elapsed = time.monotonic_ns() // 1_000_000 - start_ms
            emit_trace(
                {
                    "type": "tool.end",
                    "session_key": self.session_key,
                    "tool": tool_call.function.name,
                    "tool_call_id": tool_call.id,
                    "duration_ms": elapsed,
                    "cpu_ms": (time.process_time_ns() - cpu_start_ns) / 1_000_000,
                    "input_chars": len(tool_call.function.arguments or ""),
                    "output_chars": len(result.content or ""),
                    "input_bytes": result.meta.get("input_bytes") if result.meta else None,
                    "success": result.success,
                }
            )
            return tool_call, args, tool, result, elapsed

    async def _invoke_tool_handler(
        self,
        tool_call: Any,
        args: dict[str, Any],
        tool: Any,
        *,
        timeout_sec: int,
    ) -> ToolResult:
        """调用工具 handler，并把已知故障转换为稳定 ToolResult。"""
        try:
            return await asyncio.wait_for(tool.handler(args, self.ctx), timeout=timeout_sec)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return self._tool_error_result(
                tool_call,
                args,
                "TimeoutError",
                f"工具超时（{timeout_sec}s）: {tool_call.function.name}",
                f"工具执行超过 {timeout_sec}s 超时限制",
                user_error=False,
            )
        except PermissionError as error:
            return self._tool_error_result(
                tool_call,
                args,
                "PermissionError",
                f"权限拒绝: {error}",
                str(error),
                user_error=True,
            )
        except FileNotFoundError as error:
            return self._tool_error_result(
                tool_call,
                args,
                "FileNotFoundError",
                f"文件不存在: {error}",
                str(error),
                user_error=True,
            )
        except Exception as error:
            user_error = isinstance(
                error,
                (
                    ValueError,
                    TypeError,
                    KeyError,
                    json.JSONDecodeError,
                    SandboxViolationError,
                    FeishuConfigMissingError,
                    LarkOapiMissingError,
                ),
            )
            return self._tool_error_result(
                tool_call,
                args,
                type(error).__name__,
                f"执行异常: {error}",
                str(error),
                user_error=user_error,
                traceback_str=None if user_error else traceback.format_exc(),
            )

    def _tool_error_result(
        self,
        tool_call: Any,
        args: dict[str, Any],
        error_type: str,
        display: str,
        error_message: str,
        *,
        user_error: bool,
        traceback_str: str | None = None,
    ) -> ToolResult:
        """记录工具故障并构建统一失败结果。"""
        _log_tool_error(
            tool_name=tool_call.function.name,
            tool_call_id=tool_call.id,
            args=args,
            session_key=self.session_key,
            error_type=error_type,
            error_message=error_message,
            is_user_error=user_error,
            traceback_str=traceback_str,
        )
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} {display}",
            meta={"error_type": error_type},
        )

    async def _execute_pending_tools(
        self,
        pending: list[tuple[Any, dict[str, Any], Any]],
        *,
        timeout_sec: int,
        thinking_header: str,
    ) -> list[Any]:
        """按配置并行或串行执行工具，单工具故障不取消兄弟调用。"""
        calls = [
            self._execute_tool(
                tool_call,
                args,
                tool,
                timeout_sec=timeout_sec,
                thinking_header=thinking_header,
            )
            for tool_call, args, tool in pending
        ]
        if self.agent_config.allow_parallel_tools and len(calls) > 1:
            return list(await asyncio.gather(*calls, return_exceptions=True))
        outcomes: list[Any] = []
        for call in calls:
            try:
                outcomes.append(await call)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                outcomes.append(error)
        return outcomes

    async def _commit_tool_outcome(
        self,
        pending: tuple[Any, dict[str, Any], Any],
        outcome: Any,
        thinking_header: str,
    ) -> str | None:
        """把工具结果写回监控、上下文、活动日志和展示回调。"""
        tool_call, args, _tool = pending
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            result = ToolResult(success=False, content=f"工具执行异常: {outcome}")
            elapsed = 0
        else:
            _, _, _, result, elapsed = outcome
        self.turn_tool_calls.append(
            {
                "name": tool_call.function.name,
                "args": tool_call.function.arguments,
                "result": result.content,
            }
        )
        self.loop_detector.record(tool_call.function.name, args, result.content)
        self.monitor.record(
            tool_call.function.name,
            elapsed,
            result.success,
            error=result.content if not result.success else None,
        )
        out_of_budget = _append_context_or_return(
            self.context_manager,
            {"role": "tool", "tool_call_id": tool_call.id, "content": result.content},
        )
        if self.activity_log_enabled:
            await self.al.log_tool_call(
                session_key=self.session_key,
                tool_name=tool_call.function.name,
                intent=_extract_tool_intent(tool_call.function.name, args),
                args=args,
                result=result.content,
                duration_ms=elapsed,
                success=result.success,
                error_type=result.meta.get("error_type") if not result.success else None,
            )
        await self.invoke_on_tool_finish(
            tool_call.function.name,
            tool_call.function.arguments,
            result.content,
            result.success,
            thinking_header,
        )
        return out_of_budget

    async def run_tool_calls_phase(
        self, msg: Any, start_ms: int, thinking_header: str
    ) -> str | None:
        """处理 assistant 消息中的 tool_calls：入上下文、循环检测、并发执行工具并写回 tool 消息。

        Args:
            msg: LLM 返回的 assistant 消息（含 content 与 tool_calls）
            start_ms: 本轮开始时间戳（用于计算 elapsed）
            thinking_header: 当前思考分段标题（传递给工具回调）

        Returns:
            str | None: 上下文超预算时返回错误消息；正常完成返回 None
        """
        _raise_if_task_cancelled()
        out_of_budget = self._append_assistant_tool_message(msg)
        if out_of_budget:
            return out_of_budget
        pending, terminal = await self._collect_pending_tools(
            msg.tool_calls,
            start_ms=start_ms,
            thinking_header=thinking_header,
        )
        if terminal:
            return terminal
        outcomes = await self._execute_pending_tools(
            pending,
            timeout_sec=max(1, int(self.agent_config.tool_timeout)),
            thinking_header=thinking_header,
        )
        for pending_item, outcome in zip(pending, outcomes, strict=True):
            out_of_budget = await self._commit_tool_outcome(
                pending_item,
                outcome,
                thinking_header,
            )
            if out_of_budget:
                return out_of_budget
        return None


__all__ = ["ToolPhaseRunner"]
