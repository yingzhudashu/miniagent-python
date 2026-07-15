"""进程内命令工具：将 CLI/飞书共享的 ``dispatch_command`` 暴露给 Agent 工具调用。

``ToolContext.cli_loop_state`` 须由 ``run_runtime`` 注入；飞书变异命令拦截规则见
``docs/FEISHU.md``、``docs/CLI.md``。

重构说明：使用 ToolBuilder 简化工具定义。
"""

from __future__ import annotations

from typing import Any

from miniagent.agent.types.error_prefix import SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.agent.types.tool import ToolContext, ToolDefinition, ToolResult
from miniagent.assistant.tools.base import tool

CLI_DOT_TOOL_NAMES = frozenset({"run_dot_command"})


async def _run_dot_command_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """执行 ``run_dot_command``：转调 ``dispatch_command`` 并返回捕获输出。"""
    line = (args.get("line") or "").strip()
    if not line.startswith("/"):
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 参数 line 必须以 / 开头（与终端命令一致）。")

    st = ctx.cli_loop_state
    if not isinstance(st, dict) or st.get("runtime_ctx") is None:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 命令工具仅在完整进程集成（含 runtime_ctx）中可用。")

    rt = st["runtime_ctx"]
    from miniagent.assistant.engine.command_dispatch import dispatch_command

    out = await dispatch_command(
        line,
        state=st,
        engine=rt.engine,
        registry=rt.registry,
        monitor=rt.monitor,
        skill_toolboxes=st.get("skill_toolboxes") or [],
        skill_prompts=st.get("skill_prompts") or [],
        capture=True,
        allow_session_mutations_when_capture=ctx.cli_dispatch_allow_mutations,
        feishu_user_status=None,
        message_queue_abort_chat_id=ctx.message_queue_abort_chat_id,
    )
    if out is None:
        return ToolResult(success=False, content=f"{WARNING_PREFIX} 未识别的命令；请使用 /help 查看列表。")
    if out == "__EXIT__":
        return ToolResult(success=True, content=f"{SUCCESS_PREFIX} 实例已停止")

    stripped = (out or "").strip()
    if not stripped:
        return ToolResult(success=True, content="（命令执行成功，无文本输出）")

    text = out
    raw_mc = args.get("max_chars")
    if raw_mc is not None:
        try:
            cap = int(raw_mc)
        except (TypeError, ValueError):
            cap = 0
        if cap > 0 and len(text) > cap:
            text = text[:cap] + "\n\n…（输出已截断）"

    return ToolResult(success=True, content=text)


# ════════════════════════════════════════════════════════
# Tool Definition (使用 ToolBuilder)
# ════════════════════════════════════════════════════════

cli_dispatch_tools: dict[str, ToolDefinition] = {
    "run_dot_command": tool("run_dot_command", "执行与终端一致的 MiniAgent 命令，返回捕获的文本输出。支持：/help、/status、/session list、/queue status、/queue abort、/abort、/schedule list/show/add/remove/enable/disable。")
        .param("line", "string", "完整一行，必须以 / 开头")
        .optional("max_chars", "integer", "限制返回正文最大 Unicode 字符数")
        .allowlist()
        .toolbox("miniagent_shell")
        .handler(_run_dot_command_handler)
        .build(),
}

__all__ = ["cli_dispatch_tools", "CLI_DOT_TOOL_NAMES"]
