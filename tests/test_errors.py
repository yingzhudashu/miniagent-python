"""Tests for miniagent.types.errors and related integration."""

from __future__ import annotations

import os
import tempfile

import pytest

from miniagent.security.sandbox import resolve_sandbox_path
from miniagent.tools.feishu_utils import (
    check_feishu_config,
    check_lark_oapi,
    require_feishu_config,
    require_lark_oapi_installed,
)
from miniagent.tools.path_utils import resolve_path_for_tool
from miniagent.types.error_messages import (
    DEPENDENCY_LARK_OAPI_MISSING,
    FEISHU_CONFIG_MISSING,
    format_sandbox_path_violation,
)
from miniagent.types.errors import (
    FeishuConfigMissingError,
    LarkOapiMissingError,
    SandboxViolationError,
)
from miniagent.types.tool import ToolContext


class TestSandboxViolationError:
    def test_message_uses_error_messages_helper(self) -> None:
        path = "/etc/passwd"
        allowed = ["/workspace", "/tmp"]
        exc = SandboxViolationError(path, allowed)
        assert exc.path == path
        assert exc.allowed_dirs == allowed
        assert str(exc) == format_sandbox_path_violation(path, allowed)

    def test_allowed_dirs_is_copied(self) -> None:
        allowed: list[str] = ["/workspace"]
        exc = SandboxViolationError("x", allowed)
        allowed.append("/tmp")
        assert exc.allowed_dirs == ["/workspace"]

    def test_resolve_sandbox_path_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SandboxViolationError) as exc_info:
                resolve_sandbox_path("../../etc/passwd", [tmpdir])
            assert exc_info.value.path == "../../etc/passwd"
            assert tmpdir in exc_info.value.allowed_dirs


class TestFeishuErrors:
    def test_feishu_config_missing_message(self) -> None:
        assert str(FeishuConfigMissingError()) == FEISHU_CONFIG_MISSING

    def test_lark_oapi_missing_message(self) -> None:
        assert str(LarkOapiMissingError()) == DEPENDENCY_LARK_OAPI_MISSING

    def test_require_feishu_config_raises_without_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        with pytest.raises(FeishuConfigMissingError):
            require_feishu_config()

    def test_require_feishu_config_returns_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEISHU_APP_ID", "app")
        monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
        cfg = require_feishu_config()
        assert cfg.app_id == "app"
        assert cfg.app_secret == "secret"

    def test_check_feishu_config_maps_to_tool_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        cfg, err = check_feishu_config()
        assert cfg is None
        assert err is not None
        assert not err.success
        assert FEISHU_CONFIG_MISSING in err.content

    def test_require_lark_oapi_installed_raises_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "lark_oapi":
                raise ImportError("no lark_oapi")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        with pytest.raises(LarkOapiMissingError):
            require_lark_oapi_installed()

    def test_check_lark_oapi_maps_to_tool_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "lark_oapi":
                raise ImportError("no lark_oapi")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        err = check_lark_oapi()
        assert err is not None
        assert not err.success
        assert DEPENDENCY_LARK_OAPI_MISSING in err.content


class TestResolvePathForTool:
    def test_returns_tool_result_on_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = ToolContext(cwd=tmpdir, allowed_paths=[tmpdir])
            path, err = resolve_path_for_tool("../../etc/passwd", ctx)
            assert path is None
            assert err is not None
            assert not err.success
            assert "超出允许的范围" in err.content

    def test_returns_path_when_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = ToolContext(cwd=tmpdir, allowed_paths=[tmpdir])
            rel = "notes.txt"
            path, err = resolve_path_for_tool(rel, ctx)
            assert err is None
            assert path == os.path.realpath(os.path.join(tmpdir, rel))
