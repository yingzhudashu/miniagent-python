"""Tests for exec tools (exec_command).

Tests cover:
- Successful command execution
- Timeout handling
- Permission denial (sandbox mode)
- Command blocking patterns
- Shell injection detection
- Allowed command list
- Working directory handling
- Output capture (stdout/stderr)
- Exit code handling
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.agent.types.tool import ToolContext
from miniagent.assistant.tools.exec import (
    _BLOCKED_PATTERNS,
    _DEFAULT_ALLOWED_COMMANDS,
    _EXEC_MAX_OUTPUT_BYTES,
    _apply_command_security,
    _communicate_limited,
    _deny,
    _exec_handler,
    _get_allowed_commands,
    _is_command_allowed,
    exec_tools,
)

# ============================================================================
# Helper Functions
# ============================================================================


def _create_context(permission: str = "sandbox", cwd: str = "/tmp") -> ToolContext:
    """创建 ToolContext。"""
    return ToolContext(
        cwd=cwd,
        permission=permission,
        session_key="test_session",
        allowed_paths=[cwd],
    )


# ============================================================================
# Test Successful Execution
# ============================================================================


class TestExecSuccess:
    """测试成功命令执行。"""

    @pytest.mark.asyncio
    async def test_exec_simple_command_success(self) -> None:
        """简单命令应成功执行。"""
        ctx = _create_context(permission="full")

        # Mock subprocess
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                result = await _exec_handler({"command": "echo hello"}, ctx)

                assert result.success is True
                assert "output" in result.content
                assert "exit code: 0" in result.content

    @pytest.mark.asyncio
    async def test_exec_command_with_stderr(self) -> None:
        """命令 stderr 应被捕获。"""
        ctx = _create_context(permission="full")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"stdout", b"stderr output"))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                result = await _exec_handler({"command": "test"}, ctx)

                assert "stderr" in result.content
                assert "stderr output" in result.content

    @pytest.mark.asyncio
    async def test_output_capture_drains_but_retains_fixed_budget(self) -> None:
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()
        stdout.feed_data(b"a" * 700_000)
        stderr.feed_data(b"b" * 700_000)
        stdout.feed_eof()
        stderr.feed_eof()
        mock_proc = MagicMock(stdout=stdout, stderr=stderr)
        mock_proc.wait = AsyncMock(return_value=0)

        stdout_bytes, stderr_bytes, truncated = await _communicate_limited(mock_proc)

        assert len(stdout_bytes) + len(stderr_bytes) == _EXEC_MAX_OUTPUT_BYTES
        assert truncated is True

    @pytest.mark.asyncio
    async def test_exec_command_with_nonzero_exit(self) -> None:
        """非零退出码应标记为失败。"""
        ctx = _create_context(permission="full")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_proc.returncode = 1

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                result = await _exec_handler({"command": "false"}, ctx)

                assert result.success is False
                assert "exit code: 1" in result.content


# ============================================================================
# Test Timeout Handling
# ============================================================================


class TestExecTimeout:
    """测试超时处理。"""

    @pytest.mark.asyncio
    async def test_exec_command_timeout(self) -> None:
        """命令超时应返回错误。"""
        ctx = _create_context(permission="full")

        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        import asyncio

        async def slow_communicate():
            await asyncio.sleep(10)
            return (b"", b"")

        mock_proc.communicate = slow_communicate

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                result = await _exec_handler(
                    {"command": "sleep 10", "timeout": 0.1},
                    ctx,
                )

                assert result.success is False
                assert "超时" in result.content

    @pytest.mark.asyncio
    async def test_exec_default_timeout_is_30s(self) -> None:
        """默认超时应为 30 秒。"""
        ctx = _create_context(permission="full")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                with patch("asyncio.wait_for") as mock_wait_for:
                    mock_wait_for.return_value = (b"", b"")

                    await _exec_handler({"command": "test"}, ctx)

                    # 验证 wait_for 使用了 30 秒超时
                    call_args = mock_wait_for.call_args
                    assert call_args[1]["timeout"] == 30


# ============================================================================
# Test Sandbox Security
# ============================================================================


class TestExecSandboxSecurity:
    """测试沙箱安全检查。"""

    @pytest.mark.asyncio
    async def test_sandbox_blocks_dangerous_rm_rf(self) -> None:
        """沙箱应阻止 rm -rf /。"""
        ctx = _create_context(permission="sandbox")

        result = await _exec_handler({"command": "rm -rf /"}, ctx)

        assert result.success is False
        assert "拒绝" in result.content
        assert "危险操作" in result.content

    @pytest.mark.asyncio
    async def test_sandbox_blocks_dangerous_dd(self) -> None:
        """沙箱应阻止 dd if=。"""
        ctx = _create_context(permission="sandbox")

        result = await _exec_handler({"command": "dd if=/dev/zero of=/dev/sda"}, ctx)

        assert result.success is False
        assert "危险操作" in result.content

    @pytest.mark.asyncio
    async def test_sandbox_blocks_shell_injection_pipe(self) -> None:
        """沙箱应阻止管道注入。"""
        ctx = _create_context(permission="sandbox")

        result = await _exec_handler({"command": "echo hello | rm"}, ctx)

        assert result.success is False
        assert "注入" in result.content

    @pytest.mark.asyncio
    async def test_sandbox_blocks_shell_injection_backtick(self) -> None:
        """沙箱应阻止反引号注入。"""
        ctx = _create_context(permission="sandbox")

        result = await _exec_handler({"command": "echo `rm -rf /`"}, ctx)

        assert result.success is False
        assert "拒绝" in result.content or "危险" in result.content

    @pytest.mark.asyncio
    async def test_sandbox_blocks_shell_injection_dollar_subst(self) -> None:
        """沙箱应阻止 $() 注入。"""
        ctx = _create_context(permission="sandbox")

        result = await _exec_handler({"command": "echo $(rm -rf /)"}, ctx)

        assert result.success is False
        assert "拒绝" in result.content or "危险" in result.content

    def test_security_allows_python_code_and_query_string_inside_quotes(self) -> None:
        """Quoted Python syntax and URL query separators are not shell operators."""
        from miniagent.assistant.tools.exec import _apply_command_security

        command = (
            'python -c "import urllib.request; '
            "u='https://api.open-meteo.com/v1/forecast?latitude=22.5&longitude=114.0'; "
            'print(urllib.request.urlopen(u).read())"'
        )

        assert _apply_command_security(command, windows=True) is None

    def test_security_still_blocks_unquoted_chain_after_python_code(self) -> None:
        from miniagent.assistant.tools.exec import _apply_command_security

        result = _apply_command_security('python -c "print(1)" ; rm file', windows=False)

        assert result is not None
        assert result.success is False

    def test_security_blocks_substitution_inside_double_quotes(self) -> None:
        from miniagent.assistant.tools.exec import _apply_command_security

        result = _apply_command_security('echo "$(rm file)"', windows=False)

        assert result is not None
        assert result.success is False

    @pytest.mark.asyncio
    async def test_sandbox_blocks_unallowed_command(self) -> None:
        """沙箱应阻止不在允许列表中的命令。"""
        ctx = _create_context(permission="sandbox")

        result = await _exec_handler({"command": "some_random_command"}, ctx)

        assert result.success is False
        assert "不允许" in result.content or "允许" in result.content

    @pytest.mark.asyncio
    async def test_sandbox_allows_safe_command(self) -> None:
        """沙箱应允许 ls 等安全命令。"""
        ctx = _create_context(permission="sandbox")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"file1\nfile2", b""))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                result = await _exec_handler({"command": "ls -la"}, ctx)

                assert result.success is True

    @pytest.mark.asyncio
    async def test_full_permission_bypasses_sandbox(self) -> None:
        """full 权限应绕过沙箱检查。"""
        ctx = _create_context(permission="full")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                # full 权限下危险命令仍会执行（不检查）
                await _exec_handler({"command": "some_command"}, ctx)

                # 应创建子进程
                mock_create.assert_called_once()


# ============================================================================
# Test Allowed Commands
# ============================================================================


class TestAllowedCommands:
    """测试允许命令列表。"""

    def test_default_allowed_commands_include_common(self) -> None:
        """默认允许列表应包含常用命令。"""
        assert "ls" in _DEFAULT_ALLOWED_COMMANDS
        assert "cat" in _DEFAULT_ALLOWED_COMMANDS
        assert "grep" in _DEFAULT_ALLOWED_COMMANDS
        assert "python" in _DEFAULT_ALLOWED_COMMANDS
        assert "git" in _DEFAULT_ALLOWED_COMMANDS

    def test_get_allowed_commands_returns_default(self) -> None:
        """无配置时应返回默认列表。"""
        with patch("miniagent.assistant.tools.exec.get_config", return_value=""):
            result = _get_allowed_commands()
            assert result == _DEFAULT_ALLOWED_COMMANDS

    def test_get_allowed_commands_reads_config(self) -> None:
        """配置时应读取自定义列表。"""
        with patch("miniagent.assistant.tools.exec.get_config", return_value="ls,cat,grep"):
            result = _get_allowed_commands()
            assert "ls" in result
            assert "cat" in result
            assert "grep" in result
            assert len(result) == 3

    def test_get_allowed_commands_accepts_json_array_and_preserves_deny_all(self) -> None:
        """JSON configuration can explicitly narrow or disable command execution."""
        with patch(
            "miniagent.assistant.tools.exec.get_config", return_value=["ls", " git "]
        ):
            assert _get_allowed_commands() == frozenset({"ls", "git"})
        with patch("miniagent.assistant.tools.exec.get_config", return_value=[]):
            assert _get_allowed_commands() == frozenset()

    def test_invalid_allowed_commands_configuration_fails_closed(self) -> None:
        """A malformed security value must not silently restore permissive defaults."""
        with patch("miniagent.assistant.tools.exec.get_config", return_value={"ls": True}):
            assert _get_allowed_commands() == frozenset()

    @pytest.mark.parametrize("command", ["curl.exe", "CURL.EXE", "curl"])
    def test_windows_executable_alias_matches_portable_allowlist(self, command: str) -> None:
        """Windows 显式可执行扩展名应匹配无扩展名的跨平台白名单。"""
        assert _is_command_allowed(command, frozenset({"curl"}), windows=True)

    def test_windows_custom_allowlist_is_normalized_symmetrically(self) -> None:
        """自定义白名单使用带扩展名时，也应接受等价的无扩展名命令。"""
        assert _is_command_allowed("curl", frozenset({"CURL.EXE"}), windows=True)

    def test_windows_curl_exe_command_passes_security_validation(self) -> None:
        """回归：模型生成 curl.exe 时不应被默认 curl 白名单误拒绝。"""
        with patch("miniagent.assistant.tools.exec.get_config", return_value=""):
            result = _apply_command_security(
                'curl.exe "https://api.open-meteo.com/v1/forecast?latitude=24.4"',
                windows=True,
            )
        assert result is None

    @pytest.mark.parametrize("command", ["curl.ps1", "curl.py", "not-curl.exe"])
    def test_windows_alias_does_not_allow_unrelated_commands(self, command: str) -> None:
        """只放行标准 Windows 可执行扩展名，不能扩大到任意脚本或命令。"""
        assert not _is_command_allowed(command, frozenset({"curl"}), windows=True)

    def test_non_windows_matching_remains_exact_and_case_sensitive(self) -> None:
        """非 Windows 平台继续保持原有的精确匹配语义。"""
        allowed = frozenset({"curl"})
        assert _is_command_allowed("curl", allowed, windows=False)
        assert not _is_command_allowed("curl.exe", allowed, windows=False)
        assert not _is_command_allowed("CURL", allowed, windows=False)


# ============================================================================
# Test Blocked Patterns
# ============================================================================


class TestBlockedPatterns:
    """测试危险命令黑名单。"""

    def test_blocked_patterns_include_unix_dangerous(self) -> None:
        """黑名单应包含 Unix 危险命令。"""
        assert "rm -rf /" in _BLOCKED_PATTERNS
        assert "mkfs" in _BLOCKED_PATTERNS
        assert "dd if=" in _BLOCKED_PATTERNS

    def test_blocked_patterns_include_windows_dangerous(self) -> None:
        """黑名单应包含 Windows 危险命令。"""
        assert "del /s /q" in _BLOCKED_PATTERNS
        assert "format " in _BLOCKED_PATTERNS

    def test_deny_returns_error_result(self) -> None:
        """_deny 应返回错误结果。"""
        result = _deny("test command", "test reason")

        assert result.success is False
        assert "拒绝" in result.content
        assert "test reason" in result.content


# ============================================================================
# Test Empty Command
# ============================================================================


class TestEmptyCommand:
    """测试空命令处理。"""

    @pytest.mark.asyncio
    async def test_empty_command_returns_error(self) -> None:
        """空命令应返回错误。"""
        ctx = _create_context()

        result = await _exec_handler({"command": ""}, ctx)

        assert result.success is False
        assert "空" in result.content or "不能为空" in result.content

    @pytest.mark.asyncio
    async def test_whitespace_only_command_returns_error(self) -> None:
        """仅空格的命令应返回错误。"""
        ctx = _create_context()

        result = await _exec_handler({"command": "   "}, ctx)

        assert result.success is False


# ============================================================================
# Test Working Directory
# ============================================================================


class TestWorkingDirectory:
    """测试工作目录处理。"""

    @pytest.mark.asyncio
    async def test_custom_cwd_is_used(self, tmp_path: Path) -> None:
        """自定义工作目录须在 allowed_paths 内且会被使用。"""
        sub = tmp_path / "subdir"
        sub.mkdir()
        ctx = ToolContext(
            cwd=str(tmp_path),
            allowed_paths=[str(tmp_path)],
            permission="allowlist",
        )

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                await _exec_handler({"command": "echo hello", "cwd": str(sub)}, ctx)

                call_kwargs = mock_create.call_args[1]
                assert os.path.normcase(call_kwargs["cwd"]) == os.path.normcase(str(sub.resolve()))

    @pytest.mark.asyncio
    async def test_default_cwd_from_context(self, tmp_path: Path) -> None:
        """无自定义 cwd 时应使用上下文 cwd（须在 allowed_paths 内）。"""
        ctx = ToolContext(
            cwd=str(tmp_path),
            allowed_paths=[str(tmp_path)],
            permission="allowlist",
        )

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = mock_proc

            with patch("miniagent.assistant.tools.exec.deregister_process", new_callable=AsyncMock):
                await _exec_handler({"command": "echo hello"}, ctx)

                call_kwargs = mock_create.call_args[1]
                assert os.path.normcase(call_kwargs["cwd"]) == os.path.normcase(str(tmp_path.resolve()))

    @pytest.mark.asyncio
    async def test_cwd_outside_sandbox_rejected(self, tmp_path: Path) -> None:
        """工作目录越权应被拒绝。"""
        outside = tmp_path.parent / "exec_outside_sandbox"
        outside.mkdir(exist_ok=True)
        ctx = ToolContext(
            cwd=str(tmp_path),
            allowed_paths=[str(tmp_path)],
            permission="allowlist",
        )
        r = await _exec_handler({"command": "echo hello", "cwd": str(outside)}, ctx)
        assert not r.success
        assert "越权" in r.content or "超出" in r.content


class TestAllowlistProductionPath:
    """生产路径 permission=allowlist 仍应启用命令安全检查。"""

    @pytest.mark.asyncio
    async def test_allowlist_blocks_dangerous_command(self) -> None:
        ctx = _create_context(permission="allowlist")
        result = await _exec_handler({"command": "rm -rf /"}, ctx)
        assert not result.success
        assert "拒绝" in result.content

    @pytest.mark.asyncio
    async def test_command_security_enabled_for_allowlist(self) -> None:
        from miniagent.assistant.tools.exec import _command_security_enabled

        ctx = _create_context(permission="allowlist")
        assert _command_security_enabled(ctx) is True


# ============================================================================
# Test Tool Definition
# ============================================================================


class TestToolDefinition:
    """测试工具定义。"""

    def test_exec_tools_has_correct_schema(self) -> None:
        """exec_tools 应有正确的 schema。"""
        assert "exec_command" in exec_tools
        tool = exec_tools["exec_command"]

        assert tool.schema["type"] == "function"
        assert tool.schema["function"]["name"] == "exec_command"
        assert "command" in tool.schema["function"]["parameters"]["required"]

    def test_exec_tool_permission_is_allowlist(self) -> None:
        """exec_tool permission 应为 allowlist。"""
        tool = exec_tools["exec_command"]
        assert tool.permission == "allowlist"

    def test_exec_toolbox_is_exec(self) -> None:
        """exec_tool toolbox 应为 exec。"""
        tool = exec_tools["exec_command"]
        assert tool.toolbox == "exec"


# ============================================================================
# Test Exception Handling
# ============================================================================


class TestExceptionHandling:
    """测试异常处理。"""

    @pytest.mark.asyncio
    async def test_subprocess_exception_returns_error(self) -> None:
        """子进程异常应返回错误。"""
        ctx = _create_context(permission="full")

        with patch("miniagent.assistant.tools.exec.create_tracked_subprocess", new_callable=AsyncMock) as mock_create:
            mock_create.side_effect = Exception("process error")

            result = await _exec_handler({"command": "test"}, ctx)

            assert result.success is False
            assert "执行失败" in result.content


__all__ = [
    "TestExecSuccess",
    "TestExecTimeout",
    "TestExecSandboxSecurity",
    "TestAllowedCommands",
    "TestBlockedPatterns",
    "TestEmptyCommand",
    "TestWorkingDirectory",
    "TestToolDefinition",
    "TestExceptionHandling",
]
