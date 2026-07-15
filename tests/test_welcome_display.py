"""欢迎界面会话显示与 print_welcome 输出。"""

from __future__ import annotations

import builtins
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.engine.welcome import get_session_display, print_welcome

_real_import = builtins.__import__


def _import_without_rich_markdown(
    name: str,
    globals: dict | None = None,
    locals: dict | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> object:
    """测试用：模拟未安装 rich 时的 ``import rich.markdown`` 失败。"""
    if name == "rich.markdown" or (name == "rich" and "markdown" in fromlist):
        raise ImportError("rich not installed (test stub)")
    return _real_import(name, globals, locals, fromlist, level)


def test_get_session_display_uninitialized() -> None:
    assert get_session_display(None, "default") == "未初始化"
    assert get_session_display(MagicMock(), "") == "未初始化"
    assert get_session_display(MagicMock(), None) == "未初始化"  # type: ignore[arg-type]


def test_get_session_display_delegates_to_manager() -> None:
    manager = MagicMock()
    manager.get_session_display_name.return_value = "#1 工作"

    assert get_session_display(manager, "sess-abc") == "#1 工作"
    manager.get_session_display_name.assert_called_once_with("sess-abc")


def test_get_session_display_fallback_without_method() -> None:
    manager = SimpleNamespace()

    assert get_session_display(manager, "sess-abc") == "sess-abc"


def _make_print_welcome_args(**overrides: object) -> dict[str, object]:
    registry = MagicMock()
    registry.list.return_value = ["tool-a", "tool-b"]
    skill_registry = MagicMock()
    skill_registry.get_packages.return_value = []
    defaults: dict[str, object] = {
        "registry": registry,
        "skill_registry": skill_registry,
        "model": "gpt-4o-mini",
        "session_manager": MagicMock(
            get_session_display_name=MagicMock(return_value="#1 Default")
        ),
        "active_session_id": "default",
        "feishu_enabled": False,
    }
    defaults.update(overrides)
    return defaults


def test_print_welcome_output(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("miniagent.assistant.engine.welcome.get_version", lambda: "9.9.9-test")
    monkeypatch.setattr(
        "miniagent.assistant.engine.welcome.get_config",
        lambda key, default: False if key == "cli.welcome_hint" else default,
    )

    print_welcome(**_make_print_welcome_args(feishu_enabled=True))  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert "Mini Agent  v9.9.9-test" in out
    assert "gpt-4o-mini" in out
    assert "2 tools" in out
    assert "0 global skills · 0 session skills" in out
    assert "飞书" in out
    assert "#1 Default" in out
    assert "pip install" not in out


def test_print_welcome_shows_rich_hint_when_missing(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("miniagent.assistant.engine.welcome.get_version", lambda: "1.0.0")
    monkeypatch.setattr(
        "miniagent.assistant.engine.welcome.get_config",
        lambda key, default: True if key == "cli.welcome_hint" else default,
    )
    monkeypatch.setattr(builtins, "__import__", _import_without_rich_markdown)

    print_welcome(**_make_print_welcome_args())  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert 'pip install -e ".[cli]"' in out


def test_print_welcome_skips_hint_when_rich_available(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("miniagent.assistant.engine.welcome.get_version", lambda: "1.0.0")
    monkeypatch.setattr(
        "miniagent.assistant.engine.welcome.get_config",
        lambda key, default: True if key == "cli.welcome_hint" else default,
    )

    print_welcome(**_make_print_welcome_args())  # type: ignore[arg-type]

    out = capsys.readouterr().out
    assert "pip install" not in out
