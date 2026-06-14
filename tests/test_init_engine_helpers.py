"""init.py 辅助逻辑：MCP 配置解析、baseline skills、会话锁回退。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.engine.init import (
    _ensure_baseline_skills,
    _init_default_session,
    _parse_mcp_stdio_command,
    _register_mcp_tools_from_config,
)
from tests.config_helpers import install_test_config


class _FakeSessionManager:
    def __init__(self, session_ids: list[str] | None = None) -> None:
        self._session_ids = session_ids or ["default"]
        self.created: list[str] = []

    def list_all_sessions_with_info(self) -> list[dict]:
        return [{"session_id": sid} for sid in self._session_ids]

    def get_or_create(self, session_id: str, _options: object) -> None:
        self.created.append(session_id)


class _FakeChannelRouter:
    def __init__(self) -> None:
        self.bound: list[tuple[str, str]] = []
        self.primary: str | None = None

    def load_cli_session_state(self) -> dict:
        return {}

    def resolve(self, channel_id: str) -> str:
        return channel_id

    def bind(self, channel_id: str, session_id: str) -> str:
        self.bound.append((channel_id, session_id))
        return session_id

    def set_primary(self, session_id: str) -> None:
        self.primary = session_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["npx", "-y", "pkg"], ["npx", "-y", "pkg"]),
        ('["node", "server.js"]', ["node", "server.js"]),
        ([], None),
        ("", None),
        ("   ", None),
        ({"cmd": "npx"}, None),
        (None, None),
    ],
)
def test_parse_mcp_stdio_command(raw, expected) -> None:
    assert _parse_mcp_stdio_command(raw) == expected


@pytest.mark.asyncio
async def test_register_mcp_tools_from_native_json_array(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {"mcp": {"stdio_command": ["echo", "hello"]}},
    )

    registry = MagicMock()
    mock_register = AsyncMock(return_value=2)
    with patch("miniagent.mcp.runtime.register_mcp_stdio_tools", mock_register):
        await _register_mcp_tools_from_config(registry)

    mock_register.assert_awaited_once_with(registry, "echo", ["hello"])


def test_ensure_baseline_skills_restores_missing(tmp_path, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    monkeypatch.setattr(
        "miniagent.engine.init._get_skills_root_for_baseline",
        lambda: str(skills_root),
    )

    _ensure_baseline_skills()

    for name in ("skill-vetter", "skill-creator", "builtin-web"):
        assert (skills_root / name).is_dir()


def test_init_default_session_lock_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _lock(session_id: str) -> tuple[bool, str]:
        calls.append(session_id)
        if session_id == "default":
            return False, "被其他实例占用 (PID=9999)"
        return True, ""

    monkeypatch.setattr(
        "miniagent.engine.init.get_config",
        lambda key, default=None: default,
    )
    monkeypatch.setattr("miniagent.engine.session_lock.try_lock_session", _lock)

    sm = _FakeSessionManager(["default"])
    router = _FakeChannelRouter()
    sid = _init_default_session(sm, router)

    assert sid.startswith("default-")
    assert calls[0] == "default"
    assert calls[1] == sid
    assert router.primary == sid
    assert ("__cli__", sid) in router.bound
