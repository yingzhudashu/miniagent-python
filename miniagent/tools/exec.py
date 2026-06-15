"""Mini Agent Python — 命令执行工具

提供 exec_command 工具，在宿主机上执行 shell 命令。

特性：
- 自定义超时（默认 30 秒）
- 自定义工作目录（须在 ``allowed_paths`` 沙箱内）
- 分别捕获 stdout / stderr
- 安全过滤：除调试 ``ctx.permission="full"`` 外，始终启用黑名单 / 注入检测 / 命令白名单

``ToolContext.permission`` 为 executor 注入的运行时策略；生产默认 ``allowlist`` 仍会执行
上述命令安全检查。仅 ``full``（测试/调试）可跳过。

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
from miniagent.tools.base import tool
from miniagent.tools.path_utils import resolve_path_for_tool
from miniagent.types.error_prefix import ERROR_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

# 调试专用：跳过命令安全检查（勿在生产 executor 中注入）
_EXEC_DEBUG_PERMISSION = "full"

_logger = get_logger(__name__)

# ─── 安全配置 ────────────────────────────────────────────────

# 危险命令黑名单（沙箱模式下生效）
_BLOCKED_PATTERNS = [
    "rm -rf /", "rm -rf ~", "sudo rm", "mkfs", "dd if=", "> /dev/",
    ":(){ :|:& };:", "chmod -R 777", "> /etc/", "crontab",
    "del /s /q", "format ", "chkdsk /f", "bootsect", "bcdedit", "reg delete",
]

# Shell 注入检测正则
_SHELL_INJECTION_RE = re.compile(
    r"(\|\s*\w|;\s*\w|`[^`]+`|\$\([^)]+\)|\$\{[^}]+\}|\$\(\(|eval\s|exec\s|"
    r"curl\s.*\|\s*(bash|sh)|wget\s.*\|\s*(bash|sh)|chmod\s+777|nc\s+-e|base64\s+-d\s*\||<>|<\s*>)"
)

# 沙箱模式下允许的命令基础名
_DEFAULT_ALLOWED_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "grep", "find", "wc", "pwd", "echo", "date",
    "whoami", "uname", "df", "du", "ps", "uptime", "free", "top",
    "python", "python3", "pip", "pip3", "npm", "yarn", "pnpm", "node", "git",
    "curl", "wget", "mkdir", "touch", "cp", "mv", "chmod", "chown",
    "sed", "awk", "sort", "uniq", "tee", "zip", "unzip", "tar",
    "sha256sum", "md5sum", "ping", "nslookup", "dig", "tree", "file", "stat",
})


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


def _command_security_enabled(ctx: ToolContext) -> bool:
    """是否对本次 exec 启用命令安全检查（生产路径默认 True）。"""
    return getattr(ctx, "permission", "sandbox") != _EXEC_DEBUG_PERMISSION


def _validate_exec_cwd(cwd: str, ctx: ToolContext) -> tuple[str | None, ToolResult | None]:
    """校验工作目录在沙箱 ``allowed_paths`` 内。"""
    resolved, err = resolve_path_for_tool(cwd, ctx)
    if err:
        return None, err
    if not os.path.isdir(resolved):
        return None, ToolResult(success=False, content=f"{ERROR_PREFIX} 工作目录不存在: {cwd}")
    return resolved, None


def _apply_command_security(command: str) -> ToolResult | None:
    """黑名单 + 注入检测 + 命令白名单；通过则返回 None。"""
    for pattern in _BLOCKED_PATTERNS:
        if pattern in command:
            return _deny(command, f'包含危险操作 "{pattern}"')

    if _SHELL_INJECTION_RE.search(command):
        return _deny(command, "检测到可能的 shell 注入模式")

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
    return None


# ─── Handler ───────────────────────────────────────────────────

async def _exec_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """exec_command 处理器。

    使用 asyncio.create_subprocess_shell 实现异步命令执行。
    默认启用多层防御：黑名单 + 注入检测 + 命令允许列表；``permission=full`` 时跳过。
    """
    command = str(args["command"]).strip()
    if not command:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 命令不能为空")
    cwd_raw = str(args.get("cwd", "")) or ctx.cwd
    timeout = float(args.get("timeout", 30))

    cwd, cwd_err = _validate_exec_cwd(cwd_raw, ctx)
    if cwd_err:
        return cwd_err

    if _command_security_enabled(ctx):
        denied = _apply_command_security(command)
        if denied is not None:
            return denied

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
            except ProcessLookupError as e:
                _logger.debug("进程已不存在: %s", e)
            await proc.wait()
            await deregister_process(proc)
            return ToolResult(success=False, content=f"{ERROR_PREFIX} 命令执行超时 ({timeout}s)")

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

        await deregister_process(proc)
        return ToolResult(success=(code == 0), content=content)

    except Exception as e:
        return ToolResult(success=False, content=f"{ERROR_PREFIX} 执行失败: {e}")


# ─── Tool Definition ───────────────────────────────────────────

exec_tools: dict[str, ToolDefinition] = {
    "exec_command": tool("exec_command", "执行 shell 命令")
        .param("command", "string", "要执行的 shell 命令")
        .optional("cwd", "string", "工作目录（可选）")
        .optional("timeout", "number", "超时时间（秒），默认 30")
        .allowlist()
        .toolbox("exec")
        .handler(_exec_handler)
        .build(),
}

__all__ = [
    "exec_tools",
    "_command_security_enabled",
    "_apply_command_security",
    "_EXEC_DEBUG_PERMISSION",
]