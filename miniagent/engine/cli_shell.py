"""Synchronous shell command execution for the CLI ``!`` command."""

from __future__ import annotations

import os
import subprocess

from miniagent.core.constants import CLI_BASH_TIMEOUT
from miniagent.types.error_prefix import ERROR_PREFIX


def run_cli_shell_command(command: str) -> tuple[bool, str]:
    """执行用户显式输入的 shell 命令并返回格式化结果。

    这里有意保留管道、重定向等 shell 语义，但使用明确的 shell 可执行文件与参数
    列表，避免 ``shell=True`` 再经过 Python 隐式解释。该入口只供本地 ``!cmd``
    使用，Agent 工具命令仍必须经过独立的沙箱和权限策略。
    """
    shell_argv = _shell_argv(command)
    try:
        result = subprocess.run(
            shell_argv,
            capture_output=True,
            text=True,
            timeout=CLI_BASH_TIMEOUT,
        )
        output_lines = [f"⚙️ Bash: {command}"]
        if result.stdout:
            output_lines.append(result.stdout)
        if result.stderr:
            output_lines.append(f"{ERROR_PREFIX} stderr: {result.stderr}")
        if result.returncode != 0:
            output_lines.append(f"退出码: {result.returncode}")
        return result.returncode == 0, "\n".join(output_lines) + "\n"
    except subprocess.TimeoutExpired:
        return False, f"{ERROR_PREFIX} Bash超时（{CLI_BASH_TIMEOUT}s）: {command}\n"
    except Exception as error:
        return False, f"{ERROR_PREFIX} Bash错误: {error}\n"


def _shell_argv(command: str) -> list[str]:
    """返回当前平台的显式 shell 参数列表。"""
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return [os.environ.get("SHELL", "/bin/sh"), "-c", command]


__all__ = ["run_cli_shell_command"]
