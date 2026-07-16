"""答案审查与改进命令的会话协调处理器。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from miniagent.agent.constants import IMPROVE_MAX_ITERATIONS
from miniagent.agent.logging import get_logger
from miniagent.agent.prompts.improver import IMPROVE_PROMPT
from miniagent.agent.prompts.reviewer import REVIEW_ITERATION_PROMPT, REVIEW_PROMPT
from miniagent.agent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.assistant.engine.commands.output import command_writer

_logger = get_logger(__name__)


def _get_last_qa(session_manager: Any, session_id: str) -> tuple[str | None, str | None]:
    """Return the latest user/assistant pair from memory or compatible disk history."""
    session = session_manager.get(session_id)
    if session is None:
        return None, None
    history = getattr(session, "conversation_history", None) or []
    if not history:
        loader = getattr(session_manager, "load_session_history", None)
        if callable(loader):
            try:
                history = loader(session_id) or []
            except Exception as error:
                _logger.debug("读取会话历史失败: %s", error)
        if not history:
            files_path = getattr(session, "workspace_path", None) or getattr(
                session, "files_path", None
            )
            if files_path:
                history_path = os.path.join(os.path.dirname(files_path), "history.json")
                if os.path.isfile(history_path):
                    try:
                        with open(history_path, encoding="utf-8-sig") as handle:
                            document = json.load(handle)
                        history = (
                            document.get("messages", [])
                            if isinstance(document, dict)
                            else document
                        )
                    except (OSError, ValueError, TypeError) as error:
                        _logger.debug("读取兼容历史文件失败: %s", error)
                        history = []
    assistant_index = -1
    assistant_message: str | None = None
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if isinstance(message, dict) and message.get("role") == "assistant" and message.get("content"):
            assistant_message = message["content"]
            assistant_index = index
            break
    if assistant_message is None:
        return None, None
    for index in range(assistant_index - 1, -1, -1):
        message = history[index]
        if isinstance(message, dict) and message.get("role") == "user" and message.get("content"):
            return message["content"], assistant_message
    return None, assistant_message


async def _iterate_review(
    user_message: str,
    current_answer: str,
    issue_count: int,
    *,
    client: Any,
    max_iterations: int,
    write: Callable[[str, str], None],
) -> str:
    """Repeat review until clean, unchanged, unavailable, or bounded."""
    from miniagent.agent.llm_json import llm_json

    for iteration in range(1, max_iterations):
        result = await llm_json(
            prompt=f"用户问题：\n{user_message[:3000]}\n\n当前答案：\n{current_answer[:5000]}",
            system=REVIEW_ITERATION_PROMPT.replace("{prev_issue_count}", str(issue_count)),
            client=client,
        )
        if not result:
            write(f"{WARNING_PREFIX} 审查服务不可用，返回当前最佳答案", "ansired")
            break
        issues = result.get("issues", [])
        if not result.get("has_issues", False) or not issues:
            write(f"{SUCCESS_PREFIX} 第 {iteration + 1} 轮审查通过，无新问题。", "ansigreen")
            break
        issue_count = len(issues)
        improved = result.get("improved_answer")
        if not improved:
            write(
                f"{WARNING_PREFIX} 第 {iteration + 1} 轮发现 {len(issues)} 个问题，但无法生成改进答案",
                "ansired",
            )
            break
        current_answer = improved
        summary = "；".join(item.get("description", "")[:60] for item in issues[:2])
        write(
            f"🔄 第 {iteration + 1} 轮发现 {len(issues)} 个问题，继续改进：{summary}",
            "ansiyellow",
        )
    else:
        write(f"{WARNING_PREFIX} 已达到最大迭代次数（{max_iterations} 轮），返回最新答案", "ansiyellow")
    return current_answer


async def _run_review(
    user_message: str,
    assistant_message: str,
    *,
    extra_feedback: str = "",
    client: Any = None,
    term_write: Any = None,
    capture: bool = False,
    max_iterations: int = IMPROVE_MAX_ITERATIONS,
) -> str | None:
    """Review and iteratively improve one answer."""
    from miniagent.agent.llm_json import llm_json

    write = command_writer(term_write, capture=capture, logger=_logger)
    write("🔍 正在审查答案…", "ansicyan")
    prompt_parts = [
        f"用户问题：\n{user_message[:3000]}",
        f"\n当前答案：\n{assistant_message[:5000]}",
    ]
    if extra_feedback:
        prompt_parts.append(f"\n用户额外反馈：{extra_feedback}")
    result = await llm_json(prompt="\n".join(prompt_parts), system=REVIEW_PROMPT, client=client)
    if not result:
        write(f"{WARNING_PREFIX} 审查服务不可用（LLM 无响应）", "ansired")
        return None
    issues = result.get("issues", [])
    if not result.get("has_issues", False) or not issues:
        write(f"{SUCCESS_PREFIX} 未发现明显问题，答案质量良好。", "ansigreen")
        return None
    summary = "；".join(item.get("description", "")[:60] for item in issues[:3])
    write(f"{WARNING_PREFIX} 发现 {len(issues)} 个问题：{summary}", "ansiyellow")
    write("🔄 正在改进答案…", "ansicyan")
    answer = await _iterate_review(
        user_message,
        result.get("improved_answer") or assistant_message,
        len(issues),
        client=client,
        max_iterations=max_iterations,
        write=write,
    )
    write("\n--- 优化后的答案 ---", "ansigreen")
    if capture:
        return f"🔍 审查完成\n\n{answer[:2000]}"
    print(answer[:2000])
    return None


_IMPROVE_TEMPLATE = """请根据质量评估建议改进以下答案。

用户原始问题：
{user_input}

当前答案：
{current_answer}

质量评估建议：
{improve_suggestions}

改进要求：
1. 针对每条建议进行具体的改进
2. 保持答案的核心内容和结构
3. 补充遗漏的信息或细节
4. 提升答案的准确性和完整性
5. 优化表述的清晰度和专业性

返回 JSON 格式 {{"improved_answer": "改进后的完整答案"}}"""


async def _run_improve(
    user_message: str,
    assistant_message: str,
    suggestions: list[str],
    *,
    client: Any = None,
    term_write: Any = None,
    capture: bool = False,
) -> str | None:
    """Rewrite one answer from persisted quality suggestions."""
    from miniagent.agent.llm_json import llm_json

    write = command_writer(term_write, capture=capture, logger=_logger)
    write("🔄 正在根据建议改进答案…", "ansicyan")
    result = await llm_json(
        prompt=_IMPROVE_TEMPLATE.format(
            user_input=user_message[:3000],
            current_answer=assistant_message[:5000],
            improve_suggestions="\n".join(f"- {suggestion}" for suggestion in suggestions),
        ),
        system=IMPROVE_PROMPT,
        client=client,
    )
    improved_answer = result.get("improved_answer", "") if result else ""
    if not improved_answer:
        write(f"{WARNING_PREFIX} 改进失败（LLM 无响应）", "ansired")
        return None
    write(f"{SUCCESS_PREFIX} 答案已改进", "ansigreen")
    if capture:
        return f"🔄 改进完成\n\n{improved_answer[:2000]}"
    print(improved_answer)
    return improved_answer


async def handle_review(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """审查当前会话最后一轮回答，并按需迭代改进。"""
    runtime = state.get("runtime_ctx")
    manager = state.get("session_manager")
    session_id = str(state.get("active_session_id", ""))
    if runtime is None or manager is None or not session_id:
        output = f"{WARNING_PREFIX} /review 需要会话上下文和会话管理器"
    else:
        user_message, assistant_message = _get_last_qa(manager, session_id)
        if not user_message or not assistant_message:
            output = f"{WARNING_PREFIX} 当前会话无历史对话，无法审查"
        else:
            review_output = await _run_review(
                user_message,
                assistant_message,
                extra_feedback=" ".join(text.split()[1:]).strip(),
                client=getattr(runtime, "llm_client", getattr(runtime, "llm_gateway", None)),
                term_write=getattr(runtime, "cli_transcript_append", None),
                capture=capture,
            )
            output = review_output or ""
    if capture:
        return output if output is not None else ""
    if output:
        print(output)
    return None


async def handle_improve(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """依据质量建议重写当前会话最后一轮回答并持久化。"""
    from miniagent.assistant.engine.commands.session_management import cmd_improve

    runtime = state.get("runtime_ctx")
    manager = state.get("session_manager")
    session_id = str(state.get("active_session_id", ""))
    if runtime is None or manager is None or not session_id:
        output = f"{WARNING_PREFIX} /improve 需要会话上下文和会话管理器"
    else:
        parts = text.split()
        result = cmd_improve(
            manager,
            session_id,
            force="--force" in parts,
            reset="--reset" in parts,
        )
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], bool):
            output = str(result[0])
        else:
            user, assistant, suggestions = result
            improved_output = await _run_improve(
                user.get("content", ""),
                assistant.get("content", ""),
                suggestions,
                client=getattr(runtime, "llm_client", getattr(runtime, "llm_gateway", None)),
                term_write=getattr(runtime, "cli_transcript_append", None),
                capture=capture,
            )
            output = improved_output or ""
            if output:
                await _persist_improved_answer(manager, session_id, assistant, output)
    if capture:
        return output
    if output:
        print(output)
    return None


async def _persist_improved_answer(
    manager: Any,
    session_id: str,
    previous: dict[str, Any],
    improved_answer: str,
) -> None:
    """把改进答案追加到会话，并保留改进轮次元数据。"""
    session = manager.get(session_id)
    if session is None:
        return
    metadata = previous.get("metadata", {})
    round_number = metadata.get("improve_round", 0) + 1 if metadata.get("improved") else 1
    session.conversation_history.append(
        {
            "role": "assistant",
            "content": improved_answer,
            "metadata": {"improved": True, "improve_round": round_number},
        }
    )
    await manager.save_session_history_async(session_id)


__all__ = [
    "_get_last_qa",
    "_run_improve",
    "_run_review",
    "handle_improve",
    "handle_review",
]
