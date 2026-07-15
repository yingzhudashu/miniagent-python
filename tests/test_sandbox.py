"""Tests for sandbox path validation."""

import os
import tempfile
from unittest.mock import patch

import pytest

import miniagent.assistant.security as security_pkg
from miniagent.agent.types.errors import SandboxViolationError
from miniagent.assistant.security.sandbox import (
    get_default_workspace,
    is_path_allowed,
    resolve_sandbox_path,
)


class TestResolveSandboxPath:
    def test_absolute_path_inside_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.txt")
            result = resolve_sandbox_path(filepath, [tmpdir])
            assert result == os.path.realpath(filepath)

    def test_relative_path_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Change to tmpdir and test relative path
            saved = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = resolve_sandbox_path("sub/file.txt", [tmpdir])
                assert result.startswith(tmpdir)
                assert "sub" in result
            finally:
                os.chdir(saved)

    def test_parent_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SandboxViolationError):
                resolve_sandbox_path("../../etc/passwd", [tmpdir])

    def test_relative_path_blocked_when_cwd_outside_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SandboxViolationError):
                resolve_sandbox_path("sub/file.txt", [tmpdir])

    def test_prefix_collision_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sibling = tmpdir + "extra"
            os.makedirs(sibling, exist_ok=True)
            outside_file = os.path.join(sibling, "secret.txt")
            with pytest.raises(SandboxViolationError):
                resolve_sandbox_path(outside_file, [tmpdir])

    def test_empty_path_returns_cwd(self):
        # Empty path resolves to cwd, which may or may not be in allowed_dirs
        # Test that cwd IS in allowed_dirs
        cwd = os.getcwd()
        result = resolve_sandbox_path("", [cwd])
        assert result == os.path.realpath(cwd)

    def test_symlink_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "target.txt")
            link = os.path.join(tmpdir, "link.txt")
            with open(target, "w") as f:
                f.write("test")
            try:
                os.symlink(target, link)
                result = resolve_sandbox_path(link, [tmpdir])
                # Should resolve to the real path
                assert os.path.realpath(result) == os.path.realpath(target)
            except (OSError, NotImplementedError):
                # Symlinks not supported on this platform
                pytest.skip("Symlinks not supported")


class TestIsPathAllowed:
    def test_allowed_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.txt")
            assert is_path_allowed(filepath, [tmpdir]) is True

    def test_blocked_path(self):
        assert is_path_allowed("/etc/passwd", ["/tmp"]) is False

    def test_empty_allowed_dirs(self):
        assert is_path_allowed("/tmp/file", []) is False


class TestGetDefaultWorkspace:
    def test_returns_cwd_when_config_missing(self):
        with patch("miniagent.assistant.security.sandbox.get_config", return_value=None):
            assert get_default_workspace() == os.getcwd()

    def test_returns_cwd_when_config_empty(self):
        with patch("miniagent.assistant.security.sandbox.get_config", return_value=""):
            assert get_default_workspace() == os.getcwd()

    def test_returns_cwd_when_config_whitespace(self):
        with patch("miniagent.assistant.security.sandbox.get_config", return_value="   "):
            assert get_default_workspace() == os.getcwd()

    def test_returns_configured_path(self):
        with patch("miniagent.assistant.security.sandbox.get_config", return_value="/custom/workspace"):
            assert get_default_workspace() == "/custom/workspace"

    def test_strips_configured_path(self):
        with patch("miniagent.assistant.security.sandbox.get_config", return_value="  /custom/ws  "):
            assert get_default_workspace() == "/custom/ws"


class TestSecurityPackageExports:
    def test_is_path_allowed_exported_from_package(self):
        assert security_pkg.is_path_allowed is is_path_allowed
