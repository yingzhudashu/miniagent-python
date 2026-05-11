"""进程内点命令工具：将 CLI/飞书共享的 ``dispatch_command`` 暴露给 Agent 工具调用。

``ToolContext.cli_loop_state`` 须由 ``unified_main`` 注入；飞书变异命令拦截规则见
``docs/FEISHU.md``、``docs/CLI.md``。
"""

from __future__ import annotations

from typing import Any

from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_run_dot_command_schema = {
    "type": "function",
    "function": {
        "name": "run_dot_command",
        "description": (
            "执行与终端一致的 MiniAgent 点命令，返回捕获的文本输出。"
            "支持：.help、.status、.session list、.queue status、.queue abort、.abort、"
            ".schedule list | .schedule show <id> | .schedule add | .schedule remove | "
            ".schedule enable | .schedule disable。"
            "其中 .schedule add 必须使用「空格双连字符空格」分隔参数区与 prompt，"
            "示例：.schedule add myid every 300 primary -- 请每5分钟总结当前会话。"
            "飞书场景下：.session 的切换/创建等、以及 .schedule 的 add/remove/enable/disable 会被拒绝"
            "（仅允许 .schedule list/show）；本地 CLI 对话中 Agent 可执行上述变异命令。"
            "飞书且已注入 receive_chat_id 时，.abort / .queue abort 作用于当前群/私聊的消息队列；否则作用于 CLI 队列。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "line": {
                    "type": "string",
                    "description": (
                        "完整一行，必须以 . 开头；例如 .session list、.schedule list、"
                        ".schedule add job1 every 60 primary -- 你的 prompt"
                    ),
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        "可选。限制返回正文最大 Unicode 字符数；超出则截断并追加省略提示。"
                        "省略则不截断。"
                    ),
                },
            },
            "required": ["line"],
        },
    },
}


async def _run_dot_command_handler(
    args: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    line = (args.get("line") or "").strip()
    if not line.startswith("."):
        return ToolResult(
            success=False,
            content="⚠️ 参数 line 必须以 . 开头（与终端点命令一致）。",
        )

    st = ctx.cli_loop_state
    if not isinstance(st, dict) or st.get("runtime_ctx") is None:
        return ToolResult(
            success=False,
            content="⚠️ 点命令工具仅在完整进程集成（含 runtime_ctx）中可用。",
        )

    rt = st["runtime_ctx"]
    from miniagent.engine.command_dispatch import dispatch_command

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
        return ToolResult(
            success=False,
            content="⚠️ 未识别的点命令；请使用 .help 查看列表。",
        )
    stripped = (out or "").strip()
    if not stripped:
        return ToolResult(
            success=True,
            content="（命令执行成功，无文本输出）",
        )

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


cli_dispatch_tools: dict[str, ToolDefinition] = {
    "run_dot_command": ToolDefinition(
        schema=_run_dot_command_schema,
        handler=_run_dot_command_handler,
        permission="allowlist",
        help_text="执行进程内点命令（含 .schedule 定时任务；飞书下部分子命令受限）",
        toolbox=None,
    ),
}

CLI_DOT_TOOL_NAMES = frozenset(cli_dispatch_tools.keys())

__all__ = ["cli_dispatch_tools", "CLI_DOT_TOOL_NAMES"]
