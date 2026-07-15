"""执行阶段稳定 system 前缀与动态 user 上下文构造器。"""

from __future__ import annotations

import os

from miniagent.agent.timezone import format_agent_timezone_rule_context


def build_stable_execution_system_prompt(
    *,
    agent_identity: str,
    caller_system_prompt: str | None,
) -> str:
    """构建适合模型前缀缓存的稳定执行阶段 system prompt。

    本函数只接收低频变化的身份、调用方规则、文件边界和时区解释规则；当前
    请求、检索结果和时间必须放入 ``build_current_turn_user_context``。
    """
    parts: list[str] = [agent_identity.strip()]
    if caller_system_prompt and caller_system_prompt.strip():
        parts.append(caller_system_prompt.strip())
    parts.append(
        "## 文件与工具路径规则\n"
        "当本轮用户上下文提供默认文件根目录时，read_file、write_file、list_dir、"
        "edit_file 等工具的相对路径参数均相对于该目录；不要使用 `../` 等方式"
        "逃逸到该目录之外。如需参考会话上传文件，可使用 read_file 等工具读取。"
    )
    parts.append(format_agent_timezone_rule_context())
    return "\n\n".join(part for part in parts if part and part.strip())


def build_current_turn_user_context(
    *,
    user_input: str,
    plan_summary: str,
    keyword_context: str | None,
    kb_context: str | None = None,
    session_files_root: str | None = None,
    risk_level: str | None = None,
    current_time_context: str | None = None,
    output_spec_block: str | None = None,
) -> str:
    """构建每轮动态 user context，并保持历史消息语义顺序。

    ``session_files_root`` 会转为绝对路径，仅用于说明工具的相对路径基准；本函数
    不访问文件系统。空白的可选上下文不会产生空标题。
    """
    parts: list[str] = [f"用户请求：\n{user_input.strip()}"]
    summary = (plan_summary or "").strip()
    if summary:
        parts.append(f"执行计划摘要：\n{summary}")
    if output_spec_block and output_spec_block.strip():
        parts.append(output_spec_block.strip())
    if keyword_context and keyword_context.strip():
        parts.append(f"相关记忆：\n{keyword_context.strip()}")
    if kb_context and kb_context.strip():
        parts.append(f"相关知识库：\n{kb_context.strip()}")
    root = (session_files_root or "").strip()
    if root:
        parts.append(
            "当前默认文件根目录：\n"
            f"{os.path.abspath(root)}\n\n"
            "工具路径参数若为相对路径，均相对于该目录。"
        )
    risk = (risk_level or "").strip()
    if risk:
        parts.append(f"本任务风险等级：\n{risk}")
    time_context = (current_time_context or "").strip()
    if time_context:
        parts.append(f"当前时间上下文：\n{time_context}")
    return "\n\n".join(parts)


__all__ = ["build_current_turn_user_context", "build_stable_execution_system_prompt"]
