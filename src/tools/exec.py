"""Mini Agent Python — 命令执行工具 (Phase 5)

提供 exec_command 工具，在宿主机上执行 shell 命令。

特性：
- 自定义超时（默认 30 秒）
- 自定义工作目录
- 分别捕获 stdout / stderr
- 安全过滤（沙箱模式下阻止危险命令）
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from src.types.tool import ToolDefinition, ToolContext, ToolResult
from src.core.process_tracker import (
    create_tracked_subprocess,
    deregister_process,
)

# ─── Schema ──────────────────────────────────────────────

_exec_schema = {
    "type": "function",
    "function": {
        "name": "exec_command",
        "description": "执行 shell 命令",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "cwd": {"type": "string", "description": "工作目录（可选）"},
                "timeout": {"type": "number", "description": "超时时间（秒），默认 30"},
            },
            "required": ["command"],
        },
    },
}

# 危险命令黑名单（沙箱模式下生效）
_BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "sudo rm",
    "mkfs",
    "dd if=",
    "> /dev/",
]


async def _exec_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """exec_command 处理器。

    使用 asyncio.create_subprocess_shell 实现异步命令执行。
    """
    command = str(args["command"]).strip()
    if not command:
        return ToolResult(success=False, content="❌ 命令不能为空")
    cwd = str(args.get("cwd", "")) or ctx.cwd
    timeout = float(args.get("timeout", 30))

    # ── 安全检查 ──
    if ctx.permission == "sandbox":
        for pattern in _BLOCKED_PATTERNS:
            if pattern in command:
                return ToolResult(
                    success=False,
                    content=f"❌ 命令被拒绝: 包含危险操作 \"{pattern}\"",
                )

    try:
        # 创建子进程（自动追踪，防止孤儿）
        proc = await create_tracked_subprocess(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        # 等待完成（带超时）
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            # 从追踪列表移除
            await deregister_process(proc)
            return ToolResult(
                success=False,
                content=f"❌ 命令执行超时 ({timeout}s)",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip() if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""
        code = proc.returncode or 0

        # 拼接输出
        content = ""
        if stdout:
            content += stdout
        if stderr:
            content += f"\n[stderr]\n{stderr}"
        if not content:
            content = "(无输出)"
        content += f"\n\n[exit code: {code}]"

        # 从追踪列表移除（已完成）
        await deregister_process(proc)
        return ToolResult(success=(code == 0), content=content)

    except Exception as e:
        return ToolResult(success=False, content=f"❌ 执行失败: {e}")


# ─── 导出 ────────────────────────────────────────────────

exec_tools: dict[str, ToolDefinition] = {
    "exec_command": ToolDefinition(
        schema=_exec_schema,
        handler=_exec_handler,
        permission="allowlist",
        help_text="执行 shell 命令",
        toolbox="exec",
    ),
}

__all__ = ["exec_tools"]
