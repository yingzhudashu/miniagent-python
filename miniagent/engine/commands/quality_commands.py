"""答案审查与改进命令的会话协调处理器。"""

from __future__ import annotations

from typing import Any

from miniagent.types.error_prefix import WARNING_PREFIX


async def handle_review(
    text: str,
    *,
    state: dict[str, Any],
    capture: bool = False,
    **_kwargs: Any,
) -> str | None:
    """审查当前会话最后一轮回答，并按需迭代改进。"""
    from miniagent.engine.command_dispatch import _get_last_qa, _run_review

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
                client=getattr(runtime, "llm_client", getattr(runtime, "openai_client", None)),
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
    from miniagent.engine.cli_commands import cmd_improve
    from miniagent.engine.command_dispatch import _run_improve

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
                client=getattr(runtime, "llm_client", getattr(runtime, "openai_client", None)),
                term_write=getattr(runtime, "cli_transcript_append", None),
                capture=capture,
            )
            output = improved_output or ""
            if output:
                _persist_improved_answer(manager, session_id, assistant, output)
    if capture:
        return output
    if output:
        print(output)
    return None


def _persist_improved_answer(
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
    manager.save_session_history(session_id)


__all__ = ["handle_improve", "handle_review"]
