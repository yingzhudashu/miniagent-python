"""Synchronous shell command execution for the CLI ``!`` command."""

from __future__ import annotations

import subprocess

from miniagent.core.constants import CLI_BASH_TIMEOUT
from miniagent.types.error_prefix import ERROR_PREFIX


def run_cli_shell_command(command: str) -> tuple[bool, str]:
    """Run one shell command and return success plus formatted output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
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


__all__ = ["run_cli_shell_command"]
