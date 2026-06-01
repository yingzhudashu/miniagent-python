"""Mini Agent Python — 命令执行工具 (Phase 5)

提供 exec_command 工具，在宿主机上执行 shell 命令。

特性：
- 自定义超时（默认 30 秒）
- 自定义工作目录
- 分别捕获 stdout / stderr
- 安全过滤（沙箱模式下阻止危险命令）

命令允许清单与威胁模型见 ``docs/SECURITY.md``；子进程由 ``process`` 模块追踪以便退出时清理。
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.process import (
    create_tracked_subprocess,
    deregister_process,
)
from miniagent.types.error_prefix import ERROR_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_logger = get_logger(__name__)

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

# Shell 注入检测正则（沙箱模式下生效）
_SHELL_INJECTION_RE = re.compile(
    r"(\|\s*\w|"  # pipe to command: | ls
    r";\s*\w|"  # semicolon command: ; ls
    r"`[^`]+`|"  # backtick substitution
    r"\$\([^)]+\)|"  # $(command) substitution
    r"\$\{[^}]+\}|"  # ${var} substitution
    r"eval\s|"  # eval command
    r"exec\s|"  # exec command
    r"curl\s.*\|\s*(bash|sh)|"  # curl pipe shell
    r"wget\s.*\|\s*(bash|sh)|"  # wget pipe shell
    r"chmod\s+777|"  # chmod 777
    r"nc\s+-e|"  # netcat reverse shell
    r"base64\s+-d\s*\|"  # base64 decode pipe
    r")"
)

# 沙箱模式下允许的命令基础名
_DEFAULT_ALLOWED_COMMANDS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "find",
        "wc",
        "pwd",
        "echo",
        "date",
        "whoami",
        "uname",
        "df",
        "du",
        "ps",
        "uptime",
        "free",
        "top",
        "python",
        "python3",
        "pip",
        "pip3",
        "npm",
        "yarn",
        "pnpm",
        "node",
        "git",
        "curl",
        "wget",
        "mkdir",
        "touch",
        "cp",
        "mv",
        "chmod",
        "chown",
        "sed",
        "awk",
        "sort",
        "uniq",
        "tee",
        "zip",
        "unzip",
        "tar",
        "sha256sum",
        "md5sum",
        "ping",
        "nslookup",
        "dig",
        "tree",
        "file",
        "stat",
    }
)


def _get_allowed_commands() -> frozenset[str]:
    """从配置读取允许的命令列表，默认为内置列表。"""
    env = get_config("security.allowed_commands", "")
    if env:
        return frozenset(c.strip() for c in env.split(",") if c.strip())
    return _DEFAULT_ALLOWED_COMMANDS


def _deny(command: str, reason: str) -> ToolResult:
    """记录并返回拒绝结果。"""
    _logger.warning("命令被拒绝: command=%s reason=%s", command, reason)
    return ToolResult(success=False, content=f"{ERROR_PREFIX} 命令被拒绝: {reason}")


async def _exec_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """exec_command 处理器。

    使用 asyncio.create_subprocess_shell 实现异步命令执行。
    沙箱模式下启用多层防御：黑名单 + 注入检测 + 命令允许列表。
    """
    command = str(args["command"]).strip()
    if not command:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 命令不能为空")
    cwd = str(args.get("cwd", "")) or ctx.cwd
    timeout = float(args.get("timeout", 30))

    # ── 安全检查 ──
    if ctx.permission == "sandbox":
        # 第一层：危险命令黑名单
        for pattern in _BLOCKED_PATTERNS:
            if pattern in command:
                return _deny(command, f'包含危险操作 "{pattern}"')

        # 第二层：Shell 注入检测
        if _SHELL_INJECTION_RE.search(command):
            return _deny(command, "检测到可能的 shell 注入模式")

        # 第三层：命令允许列表
        allowed = _get_allowed_commands()
        try:
            import shlex

            parts = shlex.split(command)
        except ValueError:
            return _deny(command, "命令语法无效")

        if parts:
            base_cmd = os.path.basename(parts[0])
            if base_cmd not in allowed:
                return _deny(command, f"'{base_cmd}' 不在允许的命令列表中")

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
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
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
                content=f"{ERROR_PREFIX} 命令执行超时 ({timeout}s)",
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
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 执行失败: {e}")


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
